"""Qt Quick 3D scene embedded in a QWidget.

Owns a QQuickWidget that loads `scene.qml`. Callers set shapes via
`set_shapes(nif_path, data_dir)`; the widget handles loading the NIF,
resolving diffuse textures, decoding DDS → PNG, and handing
QQuick3DGeometry objects to the QML.

The shape-loading plumbing is the same work the Phase 0 scouting demo
did. Promoted into a reusable widget here so the preview pane can
drop it into a QSplitter alongside the config form.
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image

# PyNifly package __init__ imports bpy; bypass it for standalone use.
_PYNIFLY_DEV = r"C:\Modding\PyNifly\io_scene_nifly"
if _PYNIFLY_DEV not in sys.path:
    sys.path.insert(0, _PYNIFLY_DEV)

from PySide6.QtCore import QByteArray, QObject, QUrl, Property
from PySide6.QtGui import QVector3D
from PySide6.QtQml import QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuick3D import QQuick3DGeometry
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QWidget

from ..facegen import AssetResolver


log = logging.getLogger("furrifier.preview.scene_widget")

if getattr(sys, "frozen", False):
    # PyInstaller bundle: scene.qml is under _MEIPASS/furrifier/preview/
    QML_FILE = Path(sys._MEIPASS) / "furrifier" / "preview" / "scene.qml"  # type: ignore[attr-defined]
else:
    QML_FILE = Path(__file__).parent / "scene.qml"


# ---- geometry --------------------------------------------------------------


class FacegenShapeGeometry(QQuick3DGeometry):
    """One facegen shape as a Qt Quick 3D geometry buffer.

    Interleaved vertex buffer: POSITION(3f) + NORMAL(3f) + UV(2f) =
    32 bytes per vertex. Index buffer: uint32 triangle list.
    """

    STRIDE = (3 + 3 + 2) * 4

    def populate_from(self, shape: dict) -> None:
        verts = shape["verts"]
        tris = shape["tris"]
        n = len(verts)
        uvs = (shape["uvs"] if shape["uvs"] is not None
               else np.zeros((n, 2), dtype=np.float32))
        normals = shape["normals"]
        if normals is None:
            normals = _face_normals(verts, tris)

        interleaved = np.hstack([verts, normals, uvs]).astype(np.float32)
        vertex_bytes = QByteArray(interleaved.tobytes())
        index_bytes = QByteArray(tris.astype(np.uint32).tobytes())

        self.clear()
        self.setStride(self.STRIDE)
        self.setVertexData(vertex_bytes)
        self.setIndexData(index_bytes)
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
        self.addAttribute(
            QQuick3DGeometry.Attribute.Semantic.PositionSemantic, 0,
            QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.addAttribute(
            QQuick3DGeometry.Attribute.Semantic.NormalSemantic, 12,
            QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.addAttribute(
            QQuick3DGeometry.Attribute.Semantic.TexCoord0Semantic, 24,
            QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.addAttribute(
            QQuick3DGeometry.Attribute.Semantic.IndexSemantic, 0,
            QQuick3DGeometry.Attribute.ComponentType.U32Type)

        bmin = verts.min(axis=0)
        bmax = verts.max(axis=0)
        self.setBounds(
            QVector3D(float(bmin[0]), float(bmin[1]), float(bmin[2])),
            QVector3D(float(bmax[0]), float(bmax[1]), float(bmax[2])))
        self.update()


def _face_normals(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Per-vertex normals from triangle windings (area-weighted).
    Fallback when the shape's stored normals are missing/zero — our
    saved facegen nifs emit zero normals on morphed shapes (see the
    furrifier TODO)."""
    v0 = verts[tris[:, 0]]
    v1 = verts[tris[:, 1]]
    v2 = verts[tris[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    out = np.zeros_like(verts)
    np.add.at(out, tris[:, 0], fn)
    np.add.at(out, tris[:, 1], fn)
    np.add.at(out, tris[:, 2], fn)
    lengths = np.linalg.norm(out, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return (out / lengths).astype(np.float32)


# ---- shape loading ---------------------------------------------------------


def _rigid_preview_xform(shape, nif):
    """Return a (rotation, translation) that reproduces Skyrim's rigid
    rendering of a skinned shape. None for non-skinned shapes.

    We approximate a full linear-blend skin with a single rigid
    transform using the shape's dominant bone (highest summed weight).
    In the baked facegen nif, bone nodes are top-level children of the
    root, so `nif.nodes[bone].transform` is already in root-space.
    Skyrim's render formula collapses to:

        render_pos = bone_world @ (skin_to_bone @ v)

    which expands to `combined_rot @ v + combined_trans`.
    """
    try:
        bone_weights = shape.bone_weights
    except Exception:
        return None
    if not bone_weights:
        return None
    dominant = max(bone_weights,
                   key=lambda b: sum(w for _, w in bone_weights[b]))
    if dominant not in nif.nodes:
        return None
    bone_xf = nif.nodes[dominant].transform
    s2b = shape.get_shape_skin_to_bone(dominant)
    bone_trans = np.array(list(bone_xf.translation), dtype=np.float32)
    bone_rot = np.array([list(r) for r in bone_xf.rotation],
                        dtype=np.float32)
    s2b_trans = np.array(list(s2b.translation), dtype=np.float32)
    s2b_rot = np.array([list(r) for r in s2b.rotation], dtype=np.float32)
    combined_rot = bone_rot @ s2b_rot
    combined_trans = bone_rot @ s2b_trans + bone_trans
    return combined_rot, combined_trans


# Alpha-mode strings passed through to QML's PrincipledMaterial.alphaMode.
# Kept as strings (not enum values) because the enum lives in Qt Quick 3D;
# we map them at the QML side via a small lookup.
ALPHA_DEFAULT = "Default"
ALPHA_MASK = "Mask"
ALPHA_BLEND = "Blend"


def _alpha_from_nif_shape(shape) -> tuple[str, float]:
    """Translate a NIF shape's NiAlphaProperty into a (mode, cutoff) pair
    that QML's PrincipledMaterial understands.

    - alpha_blend=True → Blend (covers hair, scars, hairlines — partial
      transparency, sorted on render).
    - alpha_test=True, alpha_blend=False → Mask with cutoff=threshold/255
      (head, mouth, eyebrow cards — binary alpha).
    - No alpha property, or both flags off → Default (opaque).
    """
    if not shape.has_alpha_property:
        return (ALPHA_DEFAULT, 0.5)
    props = shape.alpha_property.properties
    if props.alpha_blend:
        return (ALPHA_BLEND, 0.5)
    if props.alpha_test:
        return (ALPHA_MASK, max(0.0, min(1.0, props.threshold / 255.0)))
    return (ALPHA_DEFAULT, 0.5)


def load_nif_shapes(nif_path: Path) -> List[dict]:
    """Pull verts / tris / uvs / normals / diffuse path from every
    shape in a facegen nif. Returned dicts feed FacegenShapeGeometry."""
    from pyn.pynifly import NifFile

    nif = NifFile(str(nif_path))
    shapes = []
    for shape in nif.shapes:
        verts = np.asarray(shape.verts, dtype=np.float32)
        tris = np.asarray(shape.tris, dtype=np.uint32)
        # V-flip: Qt samples with origin at the bottom-left (OpenGL
        # convention); NIF UVs are stored top-left-origin. The
        # upstream PyNifly UV-parity fix (2026-04-22) corrected the
        # write path but the read path (`shape.uvs`) still returns
        # NIF-native UVs, so the preview still needs a flip. If a
        # future PyNifly fix harmonizes both, remove this again.
        uvs = np.asarray(shape.uvs, dtype=np.float32) if shape.uvs else None
        if uvs is not None:
            uvs = uvs.copy()
            uvs[:, 1] = 1.0 - uvs[:, 1]
        # Some facegen nifs (our bake of morphed heads) ship with
        # all-zero vertex normals — treat that as "missing" so the
        # winding-based fallback picks up.
        raw_normals = (np.asarray(shape.normals, dtype=np.float32)
                       if shape.normals else None)
        if raw_normals is not None and not np.any(raw_normals):
            raw_normals = None
        # Apply the dominant-bone rigid transform so skinned shapes
        # (eyes, some hair/accessories) land in the same frame as the
        # head instead of at their authored origin. Non-skinned shapes
        # skip this and stay where they are.
        xf = _rigid_preview_xform(shape, nif)
        if xf is not None:
            rot, trans = xf
            verts = verts @ rot.T + trans
            if raw_normals is not None:
                raw_normals = raw_normals @ rot.T
        alpha_mode, alpha_cutoff = _alpha_from_nif_shape(shape)
        shapes.append({
            "name": shape.name,
            "verts": verts,
            "tris": tris,
            "uvs": uvs,
            "normals": raw_normals,
            "diffuse": shape.textures.get("Diffuse", ""),
            # Slot 6 (FacegenDetail) is populated only on the Face
            # HDPT by our facegen bake; absence = non-face shape.
            "facegen_detail": shape.textures.get("FacegenDetail", ""),
            "alpha_mode": alpha_mode,
            "alpha_cutoff": alpha_cutoff,
        })
    return shapes


def resolve_and_convert_diffuse(
        relpath: str, resolver: AssetResolver,
        temp_dir: Path) -> Optional[str]:
    """Resolve a `textures\\…` relpath via the AssetResolver (loose
    files + BSA fallback), decode the DDS via Pillow, save as PNG,
    return a file:// URL.

    Many Skyrim textures ship only in BSAs — `textures\\actors\\…\\
    tintmasks\\*.dds`, decal scars, vanilla accessory hardware. A
    raw filesystem check would leave those unrendered (untextured
    white shapes in the preview); the AssetResolver's BSA fallback
    covers them."""
    if not relpath:
        return None
    rel = relpath.lstrip("\\/").replace("\\", "/")
    if not rel.lower().startswith("textures/"):
        rel = "textures/" + rel
    src = resolver.resolve(rel)
    if src is None:
        log.warning("texture missing: %s", rel)
        return None
    png_path = temp_dir / (src.stem + ".png")
    if not png_path.exists():
        try:
            Image.open(src).convert("RGBA").save(png_path)
        except Exception as exc:
            log.warning("texture decode failed %s: %s", rel, exc)
            return None
    return QUrl.fromLocalFile(str(png_path)).toString()


# ---- context objects (QML-side models) -------------------------------------


class ShapeModel(QObject):
    """One shape exposed to QML: the geometry buffer + diffuse URL.
    Python keeps strong refs; QML just reads."""

    def __init__(self, name: str, geometry: FacegenShapeGeometry,
                 diffuse_url: str,
                 alpha_mode: str = ALPHA_DEFAULT,
                 alpha_cutoff: float = 0.5,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._name = name
        self._geometry = geometry
        self._diffuse_url = diffuse_url
        self._alpha_mode = alpha_mode
        self._alpha_cutoff = float(alpha_cutoff)

    @Property(str, constant=True)
    def name(self) -> str:
        return self._name

    @Property(QObject, constant=True)
    def geometry(self):
        return self._geometry

    @Property(str, constant=True)
    def diffuseUrl(self) -> str:
        return self._diffuse_url

    @Property(str, constant=True)
    def alphaMode(self) -> str:
        """One of "Default", "Mask", "Blend" — mapped in QML to
        PrincipledMaterial.alphaMode."""
        return self._alpha_mode

    @Property(float, constant=True)
    def alphaCutoff(self) -> float:
        return self._alpha_cutoff


class PreviewContext(QObject):
    """Owns the shape list + overall scene bounds. Mutated in place
    as the user picks different NPCs; QML re-reads via bindings."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._shapes: list[ShapeModel] = []
        self._center = QVector3D(0.0, 0.0, 0.0)
        self._radius = 50.0

    @Property(list, constant=True)
    def shapes(self) -> list:
        return self._shapes

    @Property(QVector3D, constant=True)
    def center(self) -> QVector3D:
        return self._center

    @Property(float, constant=True)
    def radius(self) -> float:
        return self._radius

    def set_scene(self, shapes: list[ShapeModel], center: QVector3D,
                  radius: float) -> None:
        # Attach all the children to us so they live as long as the
        # context does (rather than getting GC'd mid-frame).
        for s in shapes:
            s.setParent(self)
        self._shapes = shapes
        self._center = center
        self._radius = max(radius, 1.0)


# ---- the widget ------------------------------------------------------------


class FacegenSceneWidget(QWidget):
    """A QWidget that renders one facegen NIF via Qt Quick 3D.

    Call `set_nif(nif_path, data_dir)` to load and display a NIF;
    any previously-displayed scene is replaced. Textures are decoded
    into a per-widget temp cache that lives until the widget closes.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._temp_dir = Path(
            tempfile.mkdtemp(prefix="furrifier_preview_"))
        # Lazy AssetResolver. Opened on first set_nif (we don't know
        # the data_dir until then); reused across NPC picks so BSA
        # handles aren't thrashed. Closed in closeEvent.
        self._resolver: Optional[AssetResolver] = None
        self._resolver_data_dir: Optional[Path] = None

        self._ctx = PreviewContext(self)
        self._quick_widget = QQuickWidget(self)
        self._quick_widget.setResizeMode(
            QQuickWidget.ResizeMode.SizeRootObjectToView)
        # Expose the context before loading the QML, so first-binding
        # evaluations see a non-null previewCtx.
        self._quick_widget.rootContext().setContextProperty(
            "previewCtx", self._ctx)
        self._quick_widget.setSource(QUrl.fromLocalFile(str(QML_FILE)))

        # The QQuickWidget is *not* laid out. We position it manually
        # in resizeEvent so it keeps a 3:4 portrait aspect regardless
        # of the outer widget's shape (letterboxes when the pane is
        # wider-than-tall, pillarboxes when taller-than-wide).
        from PySide6.QtWidgets import QLabel
        from PySide6.QtCore import Qt as _Qt
        self._quick_widget.setParent(self)

        # Subtle top-right busy indicator. Small pill, muted colors
        # — visible enough to signal activity, not so in-your-face
        # that it obscures the preview.
        self._busy_label = QLabel("…", self._quick_widget)
        self._busy_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        self._busy_label.setStyleSheet(
            "QLabel { background-color: rgba(0, 0, 0, 110); "
            "color: rgba(255, 255, 255, 200); border-radius: 6px; "
            "padding: 3px 10px; font-size: 9pt; }")
        self._busy_label.hide()
        self._busy_label.adjustSize()
        # Re-position on resize so it stays anchored top-right.
        self._quick_widget.installEventFilter(self)

    # 3:4 aspect is enforced by positioning the QQuickWidget
    # manually in resizeEvent. heightForWidth/hasHeightForWidth
    # on a QWidget inside a QSplitter aren't reliably honored, so
    # we letterbox/pillarbox inside our own bounds instead.

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_quick_widget()

    def _fit_quick_widget(self) -> None:
        """Center + size the QQuickWidget to maintain 3:4 portrait
        (width:height), letterboxing the spare area with the pane's
        background colour."""
        outer_w = self.width()
        outer_h = self.height()
        if outer_w <= 0 or outer_h <= 0:
            return
        # 3:4 portrait — width:height = 3:4 → width = height * 3/4.
        # Pick the larger dimension that fits the outer bounds.
        if outer_w * 4 <= outer_h * 3:
            # Width is the limiting dimension.
            new_w = outer_w
            new_h = (outer_w * 4) // 3
        else:
            # Height is the limiting dimension.
            new_h = outer_h
            new_w = (outer_h * 3) // 4
        x = (outer_w - new_w) // 2
        y = (outer_h - new_h) // 2
        self._quick_widget.setGeometry(x, y, new_w, new_h)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self._quick_widget and event.type() == QEvent.Type.Resize:
            self._reposition_busy_label()
        return super().eventFilter(obj, event)

    def _reposition_busy_label(self) -> None:
        w = self._quick_widget.width()
        lw = self._busy_label.width()
        self._busy_label.move(w - lw - 8, 8)

    def set_busy(self, busy: bool, message: str = "working…") -> None:
        """Show/hide a small top-right indicator on the scene."""
        if busy:
            self._busy_label.setText(message)
            self._busy_label.adjustSize()
            self._reposition_busy_label()
            self._busy_label.show()
            self._busy_label.raise_()
        else:
            self._busy_label.hide()

    def clear(self) -> None:
        """Drop the current scene (empty the shape list)."""
        self._ctx.set_scene([], QVector3D(0, 0, 0), 50.0)

    def _build_composited_head(
            self, diffuse_relpath: str, facetint_path: Path,
            resolver: AssetResolver) -> Optional[str]:
        """For the head shape, Soft-Light-blend the FaceTint DDS onto
        the race's skin diffuse. Returns a file:// URL to the
        composited PNG cached in the widget's temp dir.

        Soft Light (Pegtop formulation) darkens where the tint is
        dark and lightens where it's light, at roughly half the
        amplitude of Overlay — close to what Skyrim's face shader
        produces in-game. We started with Overlay and it came out
        visibly more contrasty than CK's preview; Soft Light is the
        gentler match. Alpha-over would flatten diffuse detail; we
        still want pores and muscle contour surviving under the
        tint color shift.
        """
        try:
            diffuse_path = resolver.resolve(
                diffuse_relpath.lstrip("\\/").replace("\\", "/")
                if diffuse_relpath.lower().startswith("textures")
                else "textures/" + diffuse_relpath.lstrip("\\/").replace("\\", "/"))
            if diffuse_path is None:
                log.warning("head diffuse missing: %s", diffuse_relpath)
                return None
            diffuse = np.asarray(
                Image.open(diffuse_path).convert("RGBA"),
                dtype=np.float32) / 255.0
            tint_img = Image.open(facetint_path).convert("RGBA")
            # Tint is typically 512; head diffuse is typically 2048.
            # Upsample tint to match so we don't lose diffuse detail.
            if tint_img.size != (diffuse.shape[1], diffuse.shape[0]):
                tint_img = tint_img.resize(
                    (diffuse.shape[1], diffuse.shape[0]),
                    Image.Resampling.LANCZOS)
            tint = np.asarray(tint_img, dtype=np.float32) / 255.0

            # Soft Light blend (Pegtop formulation, branchless):
            #   result = (1 - 2*top) * base^2 + 2*top*base
            # At top=0.5 this is the identity (base returns unchanged);
            # as top moves away from 0.5 in either direction, the
            # result darkens or lightens smoothly, much less harshly
            # than Overlay's multiply/screen split at base=0.5.
            a = diffuse[..., :3]
            b = tint[..., :3]
            overlay = (1.0 - 2.0 * b) * a * a + 2.0 * b * a

            # Respect the tint's alpha: where it's zero (empty regions),
            # leave the raw diffuse. Where it's one, the full Overlay
            # result takes over. Partial alpha linearly crossfades.
            ta = tint[..., 3:4]
            out_rgb = a * (1.0 - ta) + overlay * ta
            # Preserve the diffuse alpha — Skyrim's face shader uses it
            # with the head's NiAlphaProperty threshold (~20/255) to
            # carve out eye sockets, nostrils, and the neck seam. If we
            # flatten it to 1.0 the preview paints those regions solid.
            out = np.concatenate(
                [out_rgb, diffuse[..., 3:4]], axis=-1)
            as_u8 = np.clip(out * 255.0, 0, 255).astype(np.uint8)

            # Stable filename per (diffuse, tint) pair. Tint DDS is
            # per-NPC in our temp dir, so each NPC pick overwrites
            # the previous composite — cache isn't cross-NPC shared,
            # which is fine for Phase 4.
            out_path = self._temp_dir / (
                f"headcomp_{Path(diffuse_relpath).stem}_"
                f"{facetint_path.stem}.png")
            Image.fromarray(as_u8, "RGBA").save(out_path)
            return QUrl.fromLocalFile(str(out_path)).toString()
        except Exception as exc:
            log.warning(
                "head diffuse+tint composite failed: %s — falling back "
                "to raw diffuse", exc)
            return None

    def _ensure_resolver(self, data_dir: Path) -> AssetResolver:
        """Open (or reopen, if data_dir changed) the asset resolver."""
        data_dir = Path(data_dir)
        if (self._resolver is None
                or self._resolver_data_dir != data_dir):
            if self._resolver is not None:
                self._resolver.close()
            self._resolver = AssetResolver.for_data_dir(data_dir)
            self._resolver_data_dir = data_dir
        return self._resolver

    def set_nif(self, nif_path: Path, data_dir: Path,
                facetint_path: Optional[Path] = None,
                preserve_camera: bool = False) -> None:
        """Load `nif_path`, resolving diffuse textures against
        `data_dir` (loose + BSA fallback via AssetResolver).
        Replaces any previously-rendered scene.

        If `facetint_path` is set (the per-NPC FaceTint DDS we baked
        alongside the nif), the head shape's diffuse is composited
        with the tint overlay so the preview reflects the NPC's
        actual skin tone + warpaint + scars. Non-head shapes are
        unaffected — tint only applies to the face.

        When `preserve_camera` is True, the QML root's orbit state
        (yaw/pitch/distance/panX/panY) is captured before the context
        property is rewired and restored afterwards. Used by the pane
        when navigating back/forward through cached entries so the
        viewer doesn't snap to default angle on each step."""
        nif_path = Path(nif_path)
        resolver = self._ensure_resolver(data_dir)
        shapes_raw = load_nif_shapes(nif_path)
        shape_models: list[ShapeModel] = []
        all_verts = []
        for s in shapes_raw:
            if len(s["verts"]) == 0 or len(s["tris"]) == 0:
                continue
            geom = FacegenShapeGeometry()
            geom.populate_from(s)
            # Face shape gets diffuse + tint composited; everyone else
            # uses the raw diffuse. The nif's shader-slot-6
            # ("FacegenDetail") is only set on the head, so that's our
            # identifier — keeps us out of HDPT-lookup land here.
            is_face = bool(s.get("facegen_detail"))
            if is_face and facetint_path is not None and facetint_path.is_file():
                diffuse_url = self._build_composited_head(
                    s["diffuse"], facetint_path, resolver) or ""
            else:
                diffuse_url = resolve_and_convert_diffuse(
                    s["diffuse"], resolver, self._temp_dir) or ""
            shape_models.append(ShapeModel(
                s["name"], geom, diffuse_url,
                alpha_mode=s.get("alpha_mode", ALPHA_DEFAULT),
                alpha_cutoff=s.get("alpha_cutoff", 0.5)))
            all_verts.append(s["verts"])

        if all_verts:
            combined = np.concatenate(all_verts, axis=0)
            center_np = combined.mean(axis=0)
            radius = float(
                np.linalg.norm(combined - center_np, axis=1).max())
            # Skyrim → Qt scene rotation: (x, y, z) → (-x, z, y).
            # Apply to the center so the camera orbits the transformed
            # bounds, not the pre-rotation ones.
            center = QVector3D(
                -float(center_np[0]),
                float(center_np[2]),
                float(center_np[1]))
        else:
            center = QVector3D(0.0, 0.0, 0.0)
            radius = 50.0

        saved_cam: Optional[tuple] = None
        if preserve_camera:
            root = self._quick_widget.rootObject()
            if root is not None:
                saved_cam = (
                    root.property("yaw"),
                    root.property("pitch"),
                    root.property("distance"),
                    root.property("panX"),
                    root.property("panY"),
                )

        self._ctx.set_scene(shape_models, center, radius)

        # Context properties are "constant" at the Property-decorator
        # level, so we can't count on QML to re-read automatically.
        # Re-setting the property forces QML to rewire its bindings.
        self._quick_widget.rootContext().setContextProperty(
            "previewCtx", self._ctx)

        if saved_cam is not None:
            # Restore AFTER the context rewire — the radius change
            # triggers QML's onRadiusChanged which resets pan/distance,
            # and re-setting the context property can re-trigger the
            # default-value bindings for yaw/pitch. Write the saved
            # values back last so they stick.
            root = self._quick_widget.rootObject()
            if root is not None:
                yaw, pitch, distance, pan_x, pan_y = saved_cam
                root.setProperty("yaw", yaw)
                root.setProperty("pitch", pitch)
                root.setProperty("distance", distance)
                root.setProperty("panX", pan_x)
                root.setProperty("panY", pan_y)

    def reframe_camera(self) -> None:
        """Reset the QML root's camera state to its default framing:
        yaw/pitch to 0 (front), pan to 0, and distance to radius*2.5
        (same formula as the QML initial binding). No-op if the QML
        root hasn't materialised yet."""
        root = self._quick_widget.rootObject()
        if root is None:
            return
        radius = root.property("radius")
        if radius is None:
            radius = self._ctx.radius
        root.setProperty("yaw", 0.0)
        root.setProperty("pitch", 0.0)
        root.setProperty("panX", 0.0)
        root.setProperty("panY", 0.0)
        root.setProperty("distance", float(radius) * 2.5)


    def closeEvent(self, event) -> None:
        # Close BSA handles, clean up the per-widget texture cache.
        if self._resolver is not None:
            self._resolver.close()
            self._resolver = None
        import shutil
        try:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass
        super().closeEvent(event)

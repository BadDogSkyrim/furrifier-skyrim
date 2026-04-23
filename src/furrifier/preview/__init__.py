"""Live-preview pane: type-ahead NPC picker + embedded 3D viewer that
bakes a single NPC's facegen on demand.

The widget tree the main window embeds via QSplitter:

    PreviewPane
      ├── NpcPickerWidget   (editable combobox, typeahead filter)
      ├── FacegenSceneWidget (QQuickWidget with a Qt Quick 3D View3D)
      └── PreviewWorker     (QThread; owns the FurrificationSession
                             and bakes NPCs in the background)
"""
from __future__ import annotations

from .pane import PreviewPane
from .scene_widget import FacegenSceneWidget
from .npc_picker import NpcPickerWidget


__all__ = [
    "PreviewPane",
    "FacegenSceneWidget",
    "NpcPickerWidget",
]

"""Merging the ARMA order across several overrides of one ARMO.

Race mods deliberately reorder an armor's addon list -- vanilla
ArmorOrcishHelmet puts the human/mer catch-all before the Khajiit addon,
and both YASCanineRaces and BDUngulates move it after. The old sort
attributed every vanilla addon to the base plugin and sorted the whole
base group last as a block, so it always reproduced VANILLA order and
those reorderings were discarded -- 31 ARMOs on a real load order.

Each override states a total order over the addons IT lists. Every
ordered pair is a constraint owned by that plugin; a later-loading
override wins a pair an earlier one ordered differently; the union is
topologically sorted. See PLAN_FURRIFIER_ARMOR_ORDER.md.

`_merge_addon_order` is pure -- (load_index, [addon key]) in, ordered
[addon key] out -- so these assert exact orders.
"""
from __future__ import annotations

import pytest

from furrifier.context import FurryContext


# Addon keys. Names chosen to read like the real case: A is the vanilla
# catch-all, K/G are the Khajiit/Argonian addons, D/C are mod-added.
A, K, G, D, C = 0x100, 0x200, 0x300, 0x400, 0x500


def merge(overrides, tie_break=None):
    """`overrides` is [(load_index, [key, ...])]. Default tie-break is
    insertion-ish: by key value, so ties are deterministic and obvious."""
    return FurryContext._merge_addon_order(
        overrides, tie_break or (lambda key: key))


class TestSingleOverride:

    def test_one_override_is_returned_verbatim(self):
        assert merge([(0, [A, K, G])]) == [A, K, G]

    def test_no_overrides_is_empty(self):
        assert merge([]) == []


class TestReordering:

    def test_a_mod_reordering_vanilla_entries_wins(self):
        """The reported bug, minimised: vanilla says A before K, the mod
        says K before A, and the mod loads later."""
        assert merge([(0, [A, K, G]),
                      (5, [K, G, A])]) == [K, G, A]

    def test_mod_addition_and_reorder_together(self):
        """ArmorOrcishHelmet in miniature: the mod adds its own addon
        AND demotes the catch-all."""
        assert merge([(0, [A, K, G]),
                      (5, [D, K, G, A])]) == [D, K, G, A]

    def test_pure_reorder_with_no_new_addon(self):
        """DLC2RR01FalxHelmet: YASCanineRaces overrides it only to move
        the catch-all to the end, adding nothing."""
        assert merge([(0, [A, G, K]),
                      (5, [G, K, A])]) == [G, K, A]


class TestComposingSeveralMods:

    def test_two_mods_each_adding_one_keep_both(self):
        """Neither mod knows about the other's addon; both survive, and
        the base order they agree on is preserved."""
        result = merge([(0, [A, K, G]),
                        (3, [D, A, K, G]),
                        (5, [C, A, K, G])])
        assert set(result) == {A, K, G, D, C}
        assert result.index(A) < result.index(K) < result.index(G)

    def test_agreed_reorder_from_both_mods_holds(self):
        result = merge([(0, [A, K, G]),
                        (3, [D, K, G, A]),
                        (5, [C, K, G, A])])
        assert result.index(K) < result.index(A)
        assert result.index(G) < result.index(A)

    def test_later_mod_wins_a_contested_pair(self):
        """Mods disagree about K vs A; the later-loading one decides."""
        assert merge([(0, [A, K]),
                      (3, [A, K]),
                      (5, [K, A])]) == [K, A]

    def test_earlier_mod_wins_nothing_it_lost(self):
        assert merge([(0, [A, K]),
                      (5, [K, A]),
                      (7, [A, K])]) == [A, K]


class TestUnconstrainedAndCycles:

    def test_addon_nobody_orders_appears_exactly_once(self):
        result = merge([(0, [A, K]), (3, [D, A])])
        assert sorted(result) == [A, K, D]
        assert len(result) == len(set(result))

    def test_tie_break_decides_unconstrained_pairs(self):
        """D and C are never mentioned together, so the tie-break -- in
        production the old mods-first-then-base sort key -- orders them,
        deterministically."""
        low_c_first = merge([(3, [D, A]), (5, [C, A])],
                            tie_break=lambda k: {C: 0, D: 1}.get(k, 9))
        assert low_c_first.index(C) < low_c_first.index(D)
        high_c_first = merge([(3, [D, A]), (5, [C, A])],
                             tie_break=lambda k: {D: 0, C: 1}.get(k, 9))
        assert high_c_first.index(D) < high_c_first.index(C)

    def test_three_way_cycle_terminates_with_a_total_order(self):
        """Rock-paper-scissors between three overrides. The earliest
        claim is the one dropped, and the result is still total."""
        result = merge([(3, [A, K]),
                        (5, [K, G]),
                        (7, [G, A])])
        assert sorted(result) == sorted([A, K, G])
        assert len(result) == len(set(result))
        # The 3-loaded claim (A before K) is the weakest and gives way.
        assert result.index(G) < result.index(A)

    def test_result_is_deterministic(self):
        overrides = [(0, [A, K, G]), (3, [D, K, G, A]), (5, [C, A, K])]
        first = merge(overrides)
        for _ in range(5):
            assert merge(overrides) == first


class TestDuplicatesWithinAnOverride:

    def test_a_repeated_entry_does_not_duplicate_the_output(self):
        """Real ARMOs occasionally list the same addon twice."""
        result = merge([(0, [A, K, A])])
        assert result.count(A) == 1
        assert sorted(result) == sorted([A, K])

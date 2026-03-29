"""Tests for configuration."""

from furrifier.config import FurrifierConfig, build_parser


class TestConfig:
    def test_defaults(self):
        config = FurrifierConfig()
        assert config.patch_filename == 'YASNPCPatch.esp'
        assert config.race_scheme == 'all_races'
        assert config.furrify_armor is True
        assert config.furrify_npcs_male is True
        assert config.furrify_npcs_female is True
        assert config.furrify_schlongs is False
        assert config.debug is False

    def test_from_args(self):
        parser = build_parser()
        args = parser.parse_args(['--patch', 'MyPatch.esp', '--scheme', 'legacy',
                                  '--no-armor', '--debug'])
        config = FurrifierConfig.from_args(args)
        assert config.patch_filename == 'MyPatch.esp'
        assert config.race_scheme == 'legacy'
        assert config.furrify_armor is False
        assert config.debug is True

    def test_parser_help(self):
        """Parser doesn't crash on --help."""
        parser = build_parser()
        # Just verify it builds without error
        assert parser.prog == 'furrifier'

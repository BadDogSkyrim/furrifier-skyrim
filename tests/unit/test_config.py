"""Tests for configuration."""

from furrifier.config import FurrifierConfig, build_parser, normalize_argv


class TestConfig:
    def test_defaults(self):
        config = FurrifierConfig()
        assert config.patch_filename == 'YASNPCPatch.esp'
        assert config.race_scheme == 'all_races'
        assert config.furrify_armor is True
        assert config.furrify_schlongs is True
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
        assert parser.prog == 'furrify_skyrim'


    def test_patch_gets_esp_extension_if_missing(self):
        parser = build_parser()
        args = parser.parse_args(['--patch', 'MyPatch'])
        config = FurrifierConfig.from_args(args)
        assert config.patch_filename == 'MyPatch.esp'


    def test_patch_keeps_esm_extension(self):
        parser = build_parser()
        args = parser.parse_args(['--patch', 'MyPatch.esm'])
        config = FurrifierConfig.from_args(args)
        assert config.patch_filename == 'MyPatch.esm'


    def test_patch_keeps_esl_extension(self):
        parser = build_parser()
        args = parser.parse_args(['--patch', 'MyPatch.esl'])
        config = FurrifierConfig.from_args(args)
        assert config.patch_filename == 'MyPatch.esl'


    def test_scheme_case_insensitive(self):
        parser = build_parser()
        args = parser.parse_args(['--scheme', 'LEGACY'])
        assert args.scheme == 'legacy'


    def test_switch_names_case_insensitive(self):
        parser = build_parser()
        args = parser.parse_args(normalize_argv(
            ['--DEBUG', '--Scheme', 'legacy', '--PATCH', 'MyPatch.esp']))
        assert args.debug is True
        assert args.scheme == 'legacy'
        assert args.patch == 'MyPatch.esp'


    def test_normalize_argv_preserves_values(self):
        """Values (paths, filenames) must not be lowercased."""
        out = normalize_argv(['--Patch', 'MyPatch.ESP', '--Data-Dir', 'C:/Skyrim/Data'])
        assert out == ['--patch', 'MyPatch.ESP', '--data-dir', 'C:/Skyrim/Data']


    def test_normalize_argv_equals_form(self):
        out = normalize_argv(['--SCHEME=Legacy'])
        assert out == ['--scheme=Legacy']


    def test_output_flag(self):
        parser = build_parser()
        args = parser.parse_args(['--output', 'C:/mods/sandbox'])
        assert args.output_dir == 'C:/mods/sandbox'


    def test_output_short_flag(self):
        parser = build_parser()
        args = parser.parse_args(['-o', 'C:/mods/sandbox'])
        assert args.output_dir == 'C:/mods/sandbox'


    def test_output_dir_alias_still_works(self):
        """Legacy `--output-dir` is a hidden alias for `--output`."""
        parser = build_parser()
        args = parser.parse_args(['--output-dir', 'C:/mods/sandbox'])
        assert args.output_dir == 'C:/mods/sandbox'


    def test_log_flag(self):
        parser = build_parser()
        args = parser.parse_args(['--log', 'run.log'])
        assert args.log_file == 'run.log'


    def test_log_file_alias_still_works(self):
        """Legacy `--log-file` is a hidden alias for `--log`."""
        parser = build_parser()
        args = parser.parse_args(['--log-file', 'run.log'])
        assert args.log_file == 'run.log'


    def test_facetint_size_default_is_none(self):
        parser = build_parser()
        args = parser.parse_args([])
        config = FurrifierConfig.from_args(args)
        assert config.facetint_size is None


    def test_facetint_size_accepts_valid_power_of_two(self):
        parser = build_parser()
        args = parser.parse_args(['--facetint-size', '1024'])
        config = FurrifierConfig.from_args(args)
        assert config.facetint_size == 1024


    def test_facetint_size_rejects_invalid(self):
        import pytest
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--facetint-size', '999'])


    def test_scheme_choices_come_from_discovery(self, monkeypatch):
        """Dropping a new scheme file in schemes/ should make it usable
        without a code change."""
        from furrifier import config as config_mod
        monkeypatch.setattr(config_mod, "list_available_schemes",
                            lambda: ["alpha", "beta"])
        parser = config_mod.build_parser()
        args = parser.parse_args(['--scheme', 'alpha'])
        assert args.scheme == 'alpha'


    def test_scheme_rejected_when_not_discovered(self, monkeypatch):
        import pytest
        from furrifier import config as config_mod
        monkeypatch.setattr(config_mod, "list_available_schemes",
                            lambda: ["alpha", "beta"])
        parser = config_mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--scheme', 'gamma'])


    def test_scheme_unconstrained_when_discovery_empty(self, monkeypatch):
        """If the schemes dir can't be located, argparse can't usefully
        validate — pass anything through and let load_scheme raise its
        own error later."""
        from furrifier import config as config_mod
        monkeypatch.setattr(config_mod, "list_available_schemes", lambda: [])
        parser = config_mod.build_parser()
        args = parser.parse_args(['--scheme', 'anything'])
        assert args.scheme == 'anything'

Bundled fallback copies of `config/*.yaml`, installed with the package.

They are only read when no `config/` directory can be found from the working
directory upwards and `HOFRADAR_CONFIG_DIR` is unset — so that `hofradar serve`
run from an odd directory still boots with the real search DNA instead of
silently reverting to hard-coded defaults.

Edit `config/` in the repository, not these. They are refreshed by
`scripts/sync_config_defaults.py`, and a test fails if the two drift apart.

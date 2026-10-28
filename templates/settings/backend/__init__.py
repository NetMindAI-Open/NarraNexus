from narranexus.sdk import Contribution, SettingField, SettingsSchema

# Values resolve NXP___PLUGIN_PKG____<KEY> env > stored row > default; secrets are encrypted at rest.
# Contribution ids are global within a slot (two plugins named "schema" cannot coexist): name yours after the plugin.
SETTINGS = (
    Contribution(
        "__PLUGIN_PKG___settings",
        lambda: SettingsSchema(
            {
                "api_key": SettingField("string", secret=True, description="Service API key"),
                "interval_minutes": SettingField("integer", default=15, description="Sync interval"),
                "region": SettingField("enum", default="eu", choices=("eu", "us")),
            }
        ),
    ),
)


def activate(ctx):
    ctx.log.info(f"interval={ctx.settings.get('interval_minutes')} region={ctx.settings.get('region')}")

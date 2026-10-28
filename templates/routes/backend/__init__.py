from fastapi import APIRouter

from narranexus.sdk import Contribution, RouterSpec, plugin_route_prefix

router = APIRouter()


@router.get("/hello")
async def hello() -> dict[str, str]:
    return {"plugin": "__PLUGIN_ID__", "message": "hello"}


# Mounted by the backend host under /api/x/__PLUGIN_ID__ (auth required by default).
# Contribution ids are global within a slot (two plugins named "api" cannot coexist): name yours after the plugin.
ROUTES = (Contribution("__PLUGIN_PKG__", lambda: RouterSpec(router, plugin_route_prefix("__PLUGIN_ID__"))),)


def activate(ctx):
    ctx.log.info("__DISPLAY_NAME__ activated")

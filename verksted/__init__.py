"""Verksted agent plugin: registers the ``verksted`` tool.

Hermes imports this package as ``hermes_plugins.verksted`` and calls ``register(ctx)`` once per
profile. Import stays cheap: Hermes modules are imported only when a handler runs.
"""

from __future__ import annotations


def register(ctx) -> None:
    from .hermes_host import HermesHost
    from .tool import DESCRIPTION, SCHEMA, TOOL_NAME, TOOLSET, make_handler

    host = HermesHost(ctx)
    ctx.register_tool(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema=SCHEMA,
        handler=make_handler(host),
        description=DESCRIPTION,
        emoji="🛠",
    )
    # A compaction can rotate a child's session id; the recursion guard follows the rotation.
    ctx.register_hook("post_compaction", host.on_post_compaction)
    _register_skill(ctx)


def _register_skill(ctx) -> None:
    """The bundled Hermes skill skills/verksted/SKILL.md, served as ``verksted:verksted``.

    A native plugin's skills/ directory is not discovered at Hermes ad2b705; only
    ``ctx.register_skill`` (hermes_cli/plugins_content.py) lists it. A broken or missing SKILL.md
    is logged and costs the tool nothing: the tool stays registered without its guidance.
    """
    import logging
    from pathlib import Path

    try:
        from ruamel.yaml import YAML  # a Hermes core dependency, like config.py's

        skill_md = Path(__file__).parent / "skills" / "verksted" / "SKILL.md"
        frontmatter = YAML(typ="safe", pure=True).load(skill_md.read_text(encoding="utf-8").split("---", 2)[1])
        ctx.register_skill("verksted", skill_md, frontmatter["description"], frontmatter)
    except Exception:
        logging.getLogger(__name__).warning("verksted: the bundled skill was not registered", exc_info=True)

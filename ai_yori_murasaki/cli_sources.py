from pathlib import Path

from .jsonio import read_json
from .sources import PROVIDERS, Sources, read_text


def add_parser(sub):
    actions = sub.add_parser("sources").add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    show = actions.add_parser("show")
    show.add_argument("--document-id", required=True)
    obsidian = actions.add_parser("import-obsidian")
    obsidian.add_argument("--vault", type=Path, required=True)
    obsidian.add_argument("--note", type=Path, required=True)
    obsidian.add_argument("--title")
    obsidian.add_argument("--link", action="append", default=[])
    for name in ("import-file", "import-mcp"):
        command = actions.add_parser(name)
        command.add_argument("--provider", choices=PROVIDERS, required=True)
        command.add_argument("--source-url", required=True)
        command.add_argument("--title", required=True)
        command.add_argument("--link", action="append", default=[])
        if name == "import-file":
            command.add_argument("--file", type=Path, required=True)
        else:
            command.add_argument("--config-file", type=Path, required=True)
            command.add_argument("--tool", required=True)
            command.add_argument("--arguments-file", type=Path, required=True)


def dispatch(args, runtime):
    sources = Sources(runtime.store)
    if args.action == "list":
        return {"sources": sources.list()}
    if args.action == "show":
        return {"source": sources.get(args.document_id)}
    if args.action == "import-obsidian":
        return sources.obsidian(args.vault, args.note, title=args.title, links=args.link)
    common = dict(provider=args.provider, source_url=args.source_url, title=args.title, links=args.link)
    if args.action == "import-file":
        return sources.add(**common, body=read_text(args.file))
    from .source_mcp import import_mcp
    return import_mcp(sources, **common, config_file=args.config_file,
                      tool=args.tool, arguments=read_json(args.arguments_file))

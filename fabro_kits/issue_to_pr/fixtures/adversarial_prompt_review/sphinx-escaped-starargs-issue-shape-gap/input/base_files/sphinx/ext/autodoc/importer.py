def format_signature(name, args):
    rendered = ", ".join(args)
    return f"{name}({rendered})"

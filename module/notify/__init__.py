def handle_notify(*args, **kwargs):
    # Lazy import onepush
    from module.notify.notify import handle_notify
    return handle_notify(*args, **kwargs)


def handle_notify_throttled(*args, **kwargs):
    # Lazy import onepush
    from module.notify.notify import handle_notify_throttled
    return handle_notify_throttled(*args, **kwargs)

import time

import onepush.core
import yaml
from onepush import get_notifier
from onepush.core import Provider
from onepush.exceptions import OnePushException
from onepush.providers.custom import Custom
from requests import Response

from module.logger import logger

onepush.core.log = logger

# onepush sets no request timeout, a dead webhook would block the task loop and the stuck timer can't fire.
ONEPUSH_REQUEST_TIMEOUT = (5, 15)  # (connect, read), seconds

# Grab the original request once; on Python 3 a staticmethod accessed on the class is already a plain function.
_original_request = getattr(Provider.request, '__func__', Provider.request)


def _request_with_timeout(method, url, **kwargs):
    """
    Wrap Provider.request to add a default timeout to all providers.

    Args:
        method (str):
        url (str):
        **kwargs: Passed to the original request, `timeout` can still be overridden.

    Returns:
        requests.Response or None: OnePush swallows request exceptions, so a timed-out push is logged as success.
    """
    kwargs.setdefault('timeout', ONEPUSH_REQUEST_TIMEOUT)
    # TODO: onepush swallows the timeout and looks like success
    return _original_request(method, url, **kwargs)


Provider.request = staticmethod(_request_with_timeout)


def handle_notify(_config: str, **kwargs) -> bool:
    try:
        config = {}
        for item in yaml.safe_load_all(_config):
            config.update(item)
    except Exception:
        logger.error("Fail to load onepush config, skip sending")
        return False
    try:
        provider_name: str = config.pop("provider", None)
        if provider_name is None:
            logger.info("No provider specified, skip sending")
            return False
        notifier: Provider = get_notifier(provider_name)
        required: list[str] = notifier.params["required"]
        config.update(kwargs)

        # pre check
        for key in required:
            if key not in config:
                logger.warning(
                    f"Notifier {notifier.name} require param '{key}' but not provided"
                )

        if isinstance(notifier, Custom):
            if "method" not in config or config["method"] == "post":
                config["datatype"] = "json"
            if not isinstance(config.get("data"), dict):
                config["data"] = {}
            if "title" in kwargs:
                config["data"]["title"] = kwargs["title"]
            if "content" in kwargs:
                config["data"]["content"] = kwargs["content"]

        if provider_name.lower() == "gocqhttp":
            access_token = config.get("access_token")
            if access_token:
                config["token"] = access_token

        resp = notifier.notify(**config)
        if isinstance(resp, Response):
            if resp.status_code != 200:
                logger.warning("Push notify failed!")
                logger.warning(f"HTTP Code:{resp.status_code}")
                return False
            else:
                if provider_name.lower() == "gocqhttp":
                    return_data: dict = resp.json()
                    if return_data["status"] == "failed":
                        logger.warning("Push notify failed!")
                        logger.warning(
                            f"Return message:{return_data['wording']}")
                        return False
    except OnePushException:
        logger.error("Push notify failed")
        return False
    except Exception as e:
        # don't show any exceptions because exceptions contain variable traceback
        logger.error(e)
        return False

    logger.info("Push notify success")
    return True


# Minimum interval in seconds between two pushes with the same key.
# Recoverable errors repeat, they must not flood the notifier.
NOTIFY_THROTTLE_INTERVAL = 600
# Wait before the next throttled push retry after a failure, so a fixed config recovers quickly.
RETRY_DELAY = 60
_last_notify_time = {}


def handle_notify_throttled(_config: str, key: str, interval: int = NOTIFY_THROTTLE_INTERVAL, **kwargs) -> bool:
    """
    Send a push at most once per `interval` seconds for the same `key`.

    Args:
        _config (str):
        key (str):
        interval (int):

    Returns:
        bool:
    """
    now = time.time()
    if now - _last_notify_time.get(key, 0) < interval:
        logger.info(f'Notify "{key}" throttled, skip')
        return False
    success = handle_notify(_config, **kwargs)
    # Only a successful push occupies the throttle window, failures retry after RETRY_DELAY.
    if success:
        _last_notify_time[key] = now
    else:
        _last_notify_time[key] = now - interval + RETRY_DELAY
    return success

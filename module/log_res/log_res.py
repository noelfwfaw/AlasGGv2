from datetime import datetime

from module.config.deep import deep_get
from module.config.utils import filepath_argument, read_file
from module.logger import logger


class LogRes:
    """
    Record resource values, such as oil, coin, gem, and so on.

    Values are written into `config.modified`, thus they will be saved into
    the config file by `Config.update()` and can be displayed on the webui
    dashboard.

    Usage:
        LogRes(config).Oil = 1234
        LogRes(config).ActionPoint = {'Value': 1000, 'Total': 1200}
    """

    def __init__(self, config):
        self.config = config
        self.groups = deep_get(
            read_file(filepath_argument('dashboard')), keys='Dashboard'
        )

    def __setattr__(self, key, value):
        if key in getattr(self, 'groups', ()):
            self.record(key, value)
        else:
            super().__setattr__(key, value)

    def record(self, group, value):
        """
        Args:
            group (str): Resource group, such as 'Oil'.
            value (int, dict): Resource value, or a dict of resource values,
                such as {'Value': 1000, 'Total': 1200}.
        """
        prefix = f'Dashboard.{group}'
        if isinstance(value, dict):
            result = {key: value[key] for key in value if key != 'Record'}
            result['Record'] = datetime.now()
        elif isinstance(value, int):
            result = {'Value': value, 'Record': datetime.now()}
        else:
            logger.error(
                f'LogRes: unsupported value type `{type(value).__name__}` of resource `{group}`'
            )
            return

        for key, value in result.items():
            self.config.modified[f'{prefix}.{key}'] = value

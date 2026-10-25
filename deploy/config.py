import copy
import subprocess
from typing import Optional, Union

from deploy.logger import logger
from deploy.utils import *


class ExecutionError(Exception):
    pass


class ConfigModel:
    # 单条外部命令的墙钟上限（秒）。这是代码级默认值，不是 deploy.yaml 配置项，
    # 避免给老的 deploy.yaml 新增必填字段；调用方可用 execute(timeout=...) 覆盖。
    # 默认取 10 分钟：pip install / git pull 可能较慢，但又不允许无限挂起。
    DEFAULT_COMMAND_TIMEOUT: int = 600

    # Git
    Repository: str = "https://github.com/LmeSzinc/AzurLaneAutoScript"
    Branch: str = "master"
    GitExecutable: str = "./toolkit/Git/mingw64/bin/git.exe"
    GitProxy: Optional[str] = None
    SSLVerify: bool = False
    AutoUpdate: bool = True

    # Python
    PythonExecutable: str = "./toolkit/python.exe"
    PypiMirror: Optional[str] = None
    InstallDependencies: bool = True
    RequirementsFile: str = "requirements.txt"

    # Adb
    AdbExecutable: str = "./toolkit/Lib/site-packages/adbutils/binaries/adb.exe"
    ReplaceAdb: bool = True
    AutoConnect: bool = True
    InstallUiautomator2: bool = True

    # Ocr
    UseOcrServer: bool = False
    StartOcrServer: bool = False
    OcrServerPort: int = 22268
    OcrClientAddress: str = "127.0.0.1:22268"

    # Update
    EnableReload: bool = True
    CheckUpdateInterval: int = 5
    AutoRestartTime: str = "03:50"

    # Misc
    DiscordRichPresence: bool = False

    # Remote Access
    EnableRemoteAccess: bool = False
    SSHUser: Optional[str] = None
    SSHServer: Optional[str] = None
    SSHExecutable: Optional[str] = None

    # Webui
    WebuiHost: str = "0.0.0.0"
    WebuiPort: int = 22267
    WebuiSSLKey: Optional[str] = None
    WebuiSSLCert: Optional[str] = None
    Language: str = "en-US"
    Theme: str = "default"
    DpiScaling: bool = True
    Password: Optional[str] = None
    CDN: Union[str, bool] = False
    Run: Optional[str] = None

    # Dynamic
    GitOverCdn: bool = False


class DeployConfig(ConfigModel):
    def __init__(self, file=DEPLOY_CONFIG):
        """
        Args:
            file (str): User deploy config.
        """
        self.file = file
        self.config = {}
        self.config_template = {}
        self.read()

        self.show_config()

    def show_config(self):
        logger.hr("Show deploy config", 1)
        for k, v in self.config.items():
            if k in ("Password", "SSHUser"):
                continue
            if self.config_template.get(k) == v:
                continue
            logger.info(f"{k}: {v}")

        logger.info(f"Rest of the configs are the same as default")

    def read(self):
        """
        Read and update deploy config, copy `self.configs` to properties.
        """
        self.config = poor_yaml_read(DEPLOY_TEMPLATE)
        self.config_template = copy.deepcopy(self.config)
        origin = poor_yaml_read(self.file)
        self.config.update(origin)

        for key, value in self.config.items():
            if hasattr(self, key):
                super().__setattr__(key, value)

        self.config_redirect()

        if self.config != origin:
            self.write()

    def write(self):
        poor_yaml_write(self.config, self.file)

    def config_redirect(self):
        """
        Redirect deploy config, must be called after each `read()`
        """
        if self.Repository in [
            'https://gitee.com/LmeSzinc/AzurLaneAutoScript',
            'https://gitee.com/lmeszinc/azur-lane-auto-script-mirror',
            'https://e.coding.net/llop18870/alas/AzurLaneAutoScript.git',
            'https://e.coding.net/saarcenter/alas/AzurLaneAutoScript.git',
            'https://git.saarcenter.com/LmeSzinc/AzurLaneAutoScript.git',
        ]:
            self.Repository = 'git://git.lyoko.io/AzurLaneAutoScript'
            self.config['Repository'] = 'git://git.lyoko.io/AzurLaneAutoScript'
        if self.PypiMirror in [
            'https://pypi.tuna.tsinghua.edu.cn/simple'
        ]:
            self.PypiMirror = 'https://mirrors.aliyun.com/pypi/simple'
            self.config['PypiMirror'] = 'https://mirrors.aliyun.com/pypi/simple'

        # Bypass webui.config.DeployConfig.__setattr__()
        # Don't write these into deploy.yaml
        super().__setattr__(
            'GitOverCdn',
            self.Repository == 'git://git.lyoko.io/AzurLaneAutoScript' and self.Branch == 'master'
        )
        if self.Repository in ['global']:
            super().__setattr__('Repository', 'https://github.com/LmeSzinc/AzurLaneAutoScript')
        if self.Repository in ['cn']:
            super().__setattr__('Repository', 'git://git.lyoko.io/AzurLaneAutoScript')

    def filepath(self, key):
        """
        Args:
            key (str):

        Returns:
            str: Absolute filepath.
        """
        return (
            os.path.abspath(os.path.join(self.root_filepath, self.config[key]))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    @cached_property
    def root_filepath(self):
        return (
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    def execute(self, command, allow_failure=False, output=True, timeout=None):
        """
        Args:
            command (str):
            allow_failure (bool):
            output(bool):
            timeout (int): Wall-clock limit in seconds, default to DEFAULT_COMMAND_TIMEOUT.

        Returns:
            bool: If success.
                Terminate installation if failed to execute and not allow_failure.
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        if not output:
            command = command + ' >nul 2>nul'
        logger.info(command)
        if timeout is None:
            timeout = self.DEFAULT_COMMAND_TIMEOUT
        error_code = self.run_command(command, timeout)
        if error_code:
            if allow_failure:
                logger.info(f"[ allowed failure ], error_code: {error_code}")
                return False
            else:
                logger.info(f"[ failure ], error_code: {error_code}")
                self.show_error(command)
                raise ExecutionError
        else:
            logger.info(f"[ success ]")
            return True

    @staticmethod
    def run_command(command, timeout):
        """
        执行一条 shell 命令并带上墙钟上限。

        原来是 `os.system()`，没有任何超时：一次卡住的 `git fetch` 会把调用它的
        线程永久挂住（WebUI 的 TaskHandler 是单线程，整个后台队列都会停摆）。

        Args:
            command (str):
            timeout (int): seconds

        Returns:
            int: exit code, -1 on timeout.
        """
        process = subprocess.Popen(command, shell=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(f"[ timeout ] command exceeded {timeout}s, terminating")
            ConfigModel.kill_process_tree(process)
            return -1

    @staticmethod
    def kill_process_tree(process):
        """
        shell=True 时直接 kill() 只杀得掉外层 cmd.exe，git.exe 之类的孙进程会残留，
        所以在 Windows 上用 taskkill /T 连子孙一起结束。
        """
        try:
            if os.name == "nt":
                subprocess.run(
                    f"taskkill /F /T /PID {process.pid}",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                process.kill()
        except Exception as e:
            logger.info(e)
        try:
            process.wait(timeout=5)
        except Exception:
            pass

    def show_error(self, command=None):
        logger.hr("Update failed", 0)
        self.show_config()
        logger.info("")
        logger.info(f"Last command: {command}")
        logger.info(
            "Please check your deploy settings in config/deploy.yaml "
            "and re-open Alas.exe"
        )
        logger.info("Take the screenshot of entire window if you need help")

"""
Copy from pywebio.platform.remote_access

* Implementation of remote access
Use https://github.com/wang0618/localshare service by running a ssh subprocess in PyWebIO application.

The stdout of ssh process is the connection info.


"""

import json
import os
import shlex
import threading
import time
from subprocess import PIPE, Popen

from module.logger import logger
from module.config.utils import random_id
from module.webui.setting import State

_ssh_process: Popen = None
_ssh_thread: threading.Thread = None
_ssh_notfound: bool = False
address: str = None

# 远程控制链接的跨进程共享文件
# Alas 实例子进程由 GUI 进程 multiprocessing 派生，拿不到 GUI 进程内的 address 全局变量，
# 因此 GUI 连上隧道后把链接写入 ./log/remote_access.url，子进程从该文件读取
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADDRESS_FILE = os.path.join(_ROOT, 'log', 'remote_access.url')

# 保活线程（独立于 WebUI 的 TaskHandler 单线程队列）
_keep_alive_thread: threading.Thread = None
_keep_alive_stop = threading.Event()
_ssh_error: str = None

KEEP_ALIVE_INTERVAL = 60  # 已连接时的巡检间隔
KEEP_ALIVE_RETRY = 5  # 未连接 / 启动失败后的重试间隔


def am_i_the_only_thread() -> bool:
    """Whether the current thread is the only non-Daemon threads in the process"""
    alive_none_daemonic_thread_cnt = sum(
        1
        for t in threading.enumerate()
        if t.is_alive() and not t.isDaemon() or t is threading.current_thread()
    )
    return alive_none_daemonic_thread_cnt == 1


def remote_access_service(
    local_host="127.0.0.1",
    local_port=22267,
    server="app.pywebio.online",
    server_port=1022,
    remote_port="/",
    setup_timeout=60,
):
    """
    Wait at most one minute to get the ssh output, if it gets a normal out, the connection is successfully established.
    Otherwise report error and kill ssh process.

    :param local_port: ssh local listen port
    :param server: ssh server domain
    :param server_port: ssh server port
    :param setup_timeout: If the service can't setup successfully in `setup_timeout` seconds, then exit.
    """
    global _ssh_process, _ssh_notfound

    bin = State.deploy_config.SSHExecutable
    cmd = f"{bin} -oStrictHostKeyChecking=no -R {remote_port}:{local_host}:{local_port} -p {server_port} {server} -- --output json"
    args = shlex.split(cmd)
    logger.debug(f"remote access service command: {cmd}")

    if _ssh_process is not None and _ssh_process.poll() is None:
        logger.warning(f"Kill previous ssh process [{_ssh_process.pid}]")
        _ssh_process.kill()
    try:
        _ssh_process = Popen(args, stdout=PIPE, stderr=PIPE)
    except FileNotFoundError as e:
        logger.critical(
            f"Cannot find SSH executable {bin}, please install OpenSSH or specify SSHExecutable in deploy.yaml"
        )
        _ssh_notfound = True
        return
    logger.info(f"remote access process pid: {_ssh_process.pid}")
    success = False

    def timeout_killer(wait_sec):
        time.sleep(wait_sec)
        if not success and _ssh_process.poll() is None:
            logger.info("Connection timeout, kill ssh process")
            _ssh_process.kill()

    threading.Thread(
        target=timeout_killer, kwargs=dict(wait_sec=setup_timeout), daemon=True
    ).start()

    stdout = _ssh_process.stdout.readline().decode("utf8")
    logger.debug(f"ssh server stdout: {stdout}")
    connection_info = {}
    try:
        connection_info = json.loads(stdout)
        success = True
    except json.decoder.JSONDecodeError:
        if not success and _ssh_process.poll() is None:
            _ssh_process.kill()

    if success:
        if connection_info.get("status", "fail") != "success":
            logger.info(
                f"Failed to establish remote access, this is the error message from service provider: {connection_info.get('message', '')}"
            )
            new_username = connection_info.get("change_username", None)
            if new_username:
                logger.info(f"Server requested to change username, change it to: {new_username}")
                State.deploy_config.SSHUser = new_username
        else:
            global address
            address = connection_info["address"]
            logger.debug(f"Remote access url: {address}")
            _save_address_file(address)

    # wait ssh or main thread exit
    while not am_i_the_only_thread() and _ssh_process.poll() is None:
        # while _ssh_process.poll() is None:
        time.sleep(1)

    if _ssh_process.poll() is None:  # main thread exit, kill ssh process
        logger.info("App process exit, killing ssh process")
        _ssh_process.kill()
    else:  # ssh process exit by itself or by timeout killer
        stderr = _ssh_process.stderr.read().decode("utf8")
        if stderr:
            logger.error(f"PyWebIO application remote access service error: {stderr}")
        else:
            logger.info("PyWebIO application remote access service exit.")
    address = None
    _save_address_file(None)


def _save_address_file(url):
    """
    把远程控制链接写入共享文件，供 Alas 实例子进程读取；传入 None 时删除文件。
    写文件失败只记 warning，不影响远程访问本身。
    """
    try:
        if url:
            os.makedirs(os.path.dirname(ADDRESS_FILE), exist_ok=True)
            with open(ADDRESS_FILE, 'w', encoding='utf-8') as f:
                f.write(url)
        elif os.path.exists(ADDRESS_FILE):
            os.remove(ADDRESS_FILE)
    except OSError as e:
        logger.warning(f"Failed to update remote access url file: {e}")


def start_remote_access_service_(**kwargs):
    logger.info("Start remote access service")
    error = None
    try:
        remote_access_service(**kwargs)
    except KeyboardInterrupt:  # ignore KeyboardInterrupt
        pass
    except Exception as e:
        error = e
        logger.exception(e)
    finally:
        if _ssh_process and _ssh_process.poll() is None:
            # 只有真的异常才说 Exception，正常退出不再打印误导性的那一行
            if error is not None:
                logger.info("Exception occurred, killing ssh process")
            else:
                logger.info("Remote access service stopped, killing ssh process")
            _ssh_process.kill()
    logger.info("Exit remote access service thread")


class ParseError(Exception):
    pass


def start_remote_access_service(**kwagrs):
    global _ssh_thread

    if _ssh_thread is not None and _ssh_thread.is_alive():
        # 已在启动中/已连接，避免保活线程与 fastapi 入口重复拉起
        logger.info("Remote access service is already running")
        return _ssh_thread

    try:
        server, server_port = State.deploy_config.SSHServer.split(":")
    except (ValueError, AttributeError):
        raise ParseError(
            f"Failed to parse SSH server [{State.deploy_config.SSHServer}]"
        )
    if State.deploy_config.WebuiHost == "0.0.0.0":
        local_host = "127.0.0.1"
    elif State.deploy_config.WebuiHost == "::":
        local_host = "[::1]"
    else:
        local_host = State.deploy_config.WebuiHost

    if State.deploy_config.SSHUser is None:
        logger.info("SSHUser is not set, generate a random one")
        State.deploy_config.SSHUser = random_id(24)

    server = f"{State.deploy_config.SSHUser}@{server}"
    kwagrs.setdefault("server", server)
    kwagrs.setdefault("server_port", server_port)
    kwagrs.setdefault("local_host", local_host)
    kwagrs.setdefault("local_port", State.deploy_config.WebuiPort)

    _ssh_thread = threading.Thread(
        target=start_remote_access_service_,
        kwargs=kwagrs,
        daemon=False,
    )
    _ssh_thread.start()
    return _ssh_thread


class RemoteAccess:
    @staticmethod
    def start_keep_alive():
        """
        用独立的 daemon 线程保活，不再挂到 TaskHandler 的单线程队列上。

        TaskHandler 是单线程串行执行，一次卡住的 check_update（git fetch）就能把
        队列堵死，保活任务永远排不上，UI 于是长期显示「未运行」。
        """
        global _keep_alive_thread
        if _keep_alive_thread is not None and _keep_alive_thread.is_alive():
            logger.info("Remote access keep-alive thread already running")
            return _keep_alive_thread
        _keep_alive_stop.clear()
        _keep_alive_thread = threading.Thread(
            target=RemoteAccess._keep_alive_loop,
            args=(_keep_alive_stop,),
            daemon=True,
            name="remote_access_keep_alive",
        )
        _keep_alive_thread.start()
        return _keep_alive_thread

    @staticmethod
    def stop_keep_alive():
        _keep_alive_stop.set()
        if _keep_alive_thread is not None and _keep_alive_thread.is_alive():
            _keep_alive_thread.join(timeout=2)

    @staticmethod
    def _keep_alive_loop(stop_event):
        """
        进程内立即尝试一次连接；失败 5 秒后重试，连上后 60 秒巡检。

        存活判定统一用 `RemoteAccess.is_alive()`，与 UI 的 `get_state()` 同一套条件，
        避免「保活认为活着 / UI 认为没运行」。
        """
        global _ssh_error
        last_error = None
        while not stop_event.is_set():
            if RemoteAccess.is_alive():
                last_error = None
                stop_event.wait(KEEP_ALIVE_INTERVAL)
                continue
            logger.info("Remote access service is not running, starting now")
            try:
                start_remote_access_service()
                _ssh_error = None
                last_error = None
                if stop_event.is_set():
                    # clearup 正在收尾时刚拉起的进程要立刻掐掉，否则它会拖住退出
                    if _ssh_process is not None and _ssh_process.poll() is None:
                        _ssh_process.kill()
                    return
            except ParseError as e:
                message = str(e)
                _ssh_error = message
                # 配置错误会每 5 秒重试一次，同样的内容不要刷 warning
                if message != last_error:
                    logger.warning(f"Remote access service config error: {message}")
                else:
                    logger.debug(f"Remote access service config error: {message}")
                last_error = message
            except Exception as e:
                _ssh_error = str(e)
                logger.warning(f"Remote access service failed to start: {e}")
            stop_event.wait(KEEP_ALIVE_RETRY)

    @staticmethod
    def kill_ssh_process():
        if RemoteAccess.is_alive():
            _ssh_process.kill()

    @staticmethod
    def is_alive():
        return (
            _ssh_thread is not None
            and _ssh_thread.is_alive()
            and _ssh_process is not None
            and _ssh_process.poll() is None
        )

    @staticmethod
    def get_state():
        if RemoteAccess.is_alive():
            if address is not None:
                return 1
            else:
                return 2
        elif _ssh_notfound:
            return 3
        else:
            return 0

    @staticmethod
    def get_error():
        """
        最近一次启动失败的描述（目前主要是 SSHServer 解析错误）。

        ParseError 不再把保活任务移出队列，而是记录在这里并保留重试；
        配置改好后下一次重试会自动清空。
        """
        return _ssh_error

    @staticmethod
    def get_entry_point():
        return address if RemoteAccess.is_alive() else None

    @staticmethod
    def get_entry_point_shared():
        """
        跨进程获取远程控制链接。

        Alas 实例子进程由 GUI 进程 multiprocessing 派生，远程隧道只在 GUI 进程内，
        子进程里 get_entry_point() 恒为 None。优先用本进程的隧道地址，
        否则读取 GUI 进程写入的共享文件（./log/remote_access.url）。
        """
        entry = RemoteAccess.get_entry_point()
        if entry:
            return entry
        try:
            if os.path.exists(ADDRESS_FILE):
                with open(ADDRESS_FILE, encoding='utf-8') as f:
                    url = f.read().strip()
                if url:
                    return url
        except OSError:
            pass
        return None

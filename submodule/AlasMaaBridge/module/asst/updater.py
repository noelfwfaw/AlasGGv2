"""
MAA 更新器。

适配 MAA v5.0+ / v6.x 的更新机制，替代旧版基于 OTA 增量包（github / ota.maa.plus）的实现。
旧实现的接口（MaaRelease 仓库、fastgit 镜像、`{cur}_{latest}` OTA 包命名）在 MAA v5 之后已全部失效。

参考 MAA 官方实现：
    ${MAA}/src/Python/asst/updater.py
    ${MAA}/src/MaaWpfGui/Models/ResourceUpdater.cs

更新分为两级：
1. 资源热更新（resource）：从 api.maa.plus 下载 tasks.json（活动关卡、新关卡导航等），
   写入 `cache/resource/tasks.json`，由 MaaCore 作为增量资源加载。体积小、随时可用，
   不涉及 dll，可在 MaaCore 已加载时执行。
2. 版本更新（package）：对比 MaaCore 版本与 api.maa.plus 版本索引，下载完整安装包
   （约 300MB）并覆盖安装目录。安装目录里 MaaCore.dll 被占用时无法覆盖，
   因此下载后先落盘，在下次启动 Alas（MaaCore 尚未加载）时自动安装。
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from typing import List, Optional, Tuple

import requests

from module.logger import logger

from .utils import Version


class Updater:
    # 版本与资源索引的 API 地址，MAA 官方已统一到 api.maa.plus
    Api = 'https://api.maa.plus'
    SummaryJson = '/MaaAssistantArknights/api/version/summary.json'
    ResourceTasksJson = '/MaaAssistantArknights/api/resource/tasks.json'

    # 完整包会整体提供的资源目录。MaaCore 会递归加载其中的文件且不允许重名，
    # 新版本删除或移动的文件若残留在这些目录中，会导致资源加载失败。
    PackageManagedDirs = re.compile(
        r'^resource/(?:global/[^/]+/resource/)?(?:tasks|template)(?=/)'
    )

    # 安装包里属于用户数据的目录，解压时必须跳过，否则会覆盖 MAA GUI 的设置与日志
    UserDataDirs = ('config/', 'logs/', 'debug/', 'cache/')

    Timeout = 30
    Headers = {
        'User-Agent': 'AlasMaaBridge/1.0 (AzurLaneAutoScript MAA plugin)'
    }

    # ------------------------------------------------
    # 基础
    # ------------------------------------------------

    def __init__(self, path, version=Version.Stable):
        """
        Args:
            path (str): MAA 安装目录（MaaCore.dll 所在目录）
            version (Version): 更新通道
        """
        self.path = os.path.abspath(path)
        self.version = version
        self.latest_version = None
        self.latest_detail = None

    def __str__(self):
        return f'<MaaUpdater {self.path}>'

    # ------------------------------------------------
    # 路径
    # ------------------------------------------------

    @property
    def cache_dir(self) -> str:
        return os.path.join(self.path, 'cache')

    @property
    def update_dir(self) -> str:
        return os.path.join(self.cache_dir, 'update')

    @property
    def pending_json(self) -> str:
        return os.path.join(self.update_dir, 'pending.json')

    @property
    def resource_tasks(self) -> str:
        """
        MAA 官方示例（Python/asst/sample.py）中的活动关卡资源路径，
        同时也是 MAA GUI 写入的位置。
        """
        return os.path.join(self.cache_dir, 'resource', 'tasks.json')

    @property
    def resource_tasks_dir(self) -> str:
        """
        MAA v6 起部分版本使用 `cache/resource/tasks/tasks.json` 这一层级，
        两个位置保持同步，避免不同版本读取到不同内容。
        """
        return os.path.join(self.cache_dir, 'resource', 'tasks', 'tasks.json')

    @property
    def etag_json(self) -> str:
        return os.path.join(self.cache_dir, 'etag.json')

    # ------------------------------------------------
    # HTTP helper
    # ------------------------------------------------

    @staticmethod
    def _read_json(file, default):
        try:
            with open(file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write_json(file, data):
        os.makedirs(os.path.dirname(file), exist_ok=True)
        temp = f'{file}.tmp'
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(temp, file)

    def _get(self, url, headers=None, stream=False, retry=3, timeout=None):
        """
        带重试的 GET 请求，全部失败时返回 None。
        """
        request_headers = dict(self.Headers)
        if headers:
            request_headers.update(headers)
        timeout = timeout or self.Timeout

        for i in range(retry):
            try:
                response = requests.get(
                    url, headers=request_headers, stream=stream, timeout=timeout)
                if response.status_code == 304:
                    return response
                response.raise_for_status()
                return response
            except Exception as e:
                logger.warning(f'MAA 更新请求失败 [{i + 1}/{retry}] {url}: {e}')
                if i < retry - 1:
                    time.sleep(2)
        return None

    # ------------------------------------------------
    # 版本信息
    # ------------------------------------------------

    @staticmethod
    def map_version_type(version) -> str:
        type_map = {
            Version.Nightly: 'alpha',
            Version.Beta: 'beta',
            Version.Stable: 'stable',
        }
        return type_map.get(version, 'stable')

    def get_current_version(self) -> Optional[str]:
        """
        读取当前 MaaCore 版本号。

        在子进程中加载动态库，避免本进程占用 MaaCore.dll 导致后续无法覆盖安装
        （与 MAA 官方 Python 接口使用子进程的做法一致）。
        """
        lib = os.path.join(self.path, 'MaaCore.dll')
        if not os.path.isfile(lib):
            lib = os.path.join(self.path, 'libMaaCore.so')
        if not os.path.isfile(lib):
            lib = os.path.join(self.path, 'libMaaCore.dylib')
        if not os.path.isfile(lib):
            logger.warning(f'未找到 MaaCore 动态库: {self.path}')
            return None

        module_dir = os.path.dirname(os.path.abspath(__file__))
        module_dir = os.path.dirname(module_dir)
        # MaaCore 依赖较新版本的 VC 运行时，而 Alas 自带 python 目录下的
        # msvcp140.dll 等版本较旧，直接加载会 [WinError 1114] 初始化失败。
        # 与 maa.py 的做法一致：先按全路径加载 System32 的运行时。
        preload = (
            'import ctypes, os, sys\n'
            'system_root = os.environ.get("SystemRoot", r"C:\\Windows")\n'
            'for name in ("vcruntime140_1.dll", "msvcp140.dll",\n'
            '             "msvcp140_1.dll", "concrt140.dll"):\n'
            '    try:\n'
            '        ctypes.WinDLL(os.path.join(system_root, "System32", name))\n'
            '    except Exception:\n'
            '        pass\n'
        ) if os.name == 'nt' else 'import sys\n'

        script = (
            preload +
            f'sys.path.insert(0, {module_dir!r})\n'
            'from asst.asst import Asst\n'
            f'Asst.load_library({self.path!r})\n'
            'sys.stdout.write(Asst().get_version())\n'
        )
        try:
            result = subprocess.run(
                [sys.executable, '-c', script],
                capture_output=True, timeout=120)
            version = result.stdout.decode('utf-8', 'ignore').strip()
            if version:
                return version
            logger.warning(
                f'读取 MaaCore 版本号失败: '
                f'{result.stderr.decode("utf-8", "ignore").strip()[-300:]}')
        except Exception as e:
            logger.warning(f'读取 MaaCore 版本号失败: {e}')
        return None

    def get_latest_version(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns:
            (latest_version, detail_url)，失败时返回 (None, None)
        """
        response = self._get(self.Api + self.SummaryJson)
        if response is None:
            logger.warning('获取 MAA 版本索引失败，请检查网络')
            return None, None
        try:
            data = response.json()
        except ValueError:
            logger.warning('MAA 版本索引解析失败')
            return None, None

        version_type = self.map_version_type(self.version)
        try:
            return data[version_type]['version'], data[version_type]['detail']
        except (KeyError, TypeError):
            logger.warning(f'MAA 版本索引中没有 {version_type} 通道')
            return None, None

    # ------------------------------------------------
    # 资源热更新
    # ------------------------------------------------

    def update_resource(self, force=False, client='Official') -> bool:
        """
        更新活动关卡等增量任务资源（tasks.json）。

        Args:
            force (bool): 忽略 ETag，强制重新下载
            client (str): 游戏服务器，外服资源随完整包发布，不提供增量更新

        Returns:
            bool: 是否发生了更新
        """
        if client not in ('Official', 'Bilibili'):
            logger.info(f'{client} 无需更新增量任务资源，外服资源随 MAA 完整包发布')
            return False

        url = self.Api + self.ResourceTasksJson
        etag_data = self._read_json(self.etag_json, {}) or {}
        etag = etag_data.get(url) if isinstance(etag_data, dict) else None

        headers = {}
        if etag and not force:
            headers['If-None-Match'] = etag

        logger.info('检查 MAA 资源更新')
        response = self._get(url, headers=headers, retry=2, timeout=10)
        if response is None:
            logger.warning('MAA 资源更新失败，将使用已有资源继续运行')
            return False
        if response.status_code == 304:
            logger.info('MAA 任务资源已是最新')
            return False

        try:
            data = response.json()
        except ValueError:
            logger.warning('MAA 任务资源解析失败，已跳过本次更新')
            return False
        if not isinstance(data, dict) or not data:
            logger.warning('MAA 任务资源内容异常，已跳过本次更新')
            return False

        updated = self._read_json(self.resource_tasks, None) != data
        for file in (self.resource_tasks, self.resource_tasks_dir):
            try:
                self._write_json(file, data)
            except OSError as e:
                logger.warning(f'写入 MAA 任务资源失败 {file}: {e}')

        new_etag = response.headers.get('ETag')
        if new_etag:
            if not isinstance(etag_data, dict):
                etag_data = {}
            etag_data[url] = new_etag
            try:
                self._write_json(self.etag_json, etag_data)
            except OSError as e:
                logger.warning(f'写入 MAA 资源 etag 失败: {e}')

        if updated:
            logger.info(f'MAA 任务资源已更新（{len(data)} 个任务）')
        else:
            logger.info('MAA 任务资源内容无变化')
        return updated

    # ------------------------------------------------
    # 版本更新
    # ------------------------------------------------

    @staticmethod
    def _platform_tag() -> str:
        system = platform.system()
        machine = platform.machine().lower()
        if system == 'Windows':
            return 'win-arm64' if machine in ('arm64', 'aarch64') else 'win-x64'
        if system == 'Linux':
            return 'linux-aarch64' if machine in ('aarch64', 'arm64') else 'linux-x86_64'
        return 'win-x64'

    def get_download_urls(self, detail_url) -> Tuple[List[str], Optional[str], int]:
        """
        从版本详情中挑选当前系统/架构的安装包。

        Returns:
            (url 列表（镜像 + GitHub）, 文件名, 文件大小)
        """
        response = self._get(detail_url)
        if response is None:
            return [], None, 0
        try:
            assets = response.json()['details']['assets']
        except (ValueError, KeyError, TypeError):
            logger.warning('MAA 版本详情解析失败')
            return [], None, 0

        tag = self._platform_tag()
        pattern = re.compile(r'^MAA-.*-' + re.escape(tag) + r'\.(zip|tar\.gz)$')
        for asset in assets:
            name = asset.get('name', '')
            if not pattern.match(name):
                continue
            # 过滤 DebugSymbol 组件包
            if 'DebugSymbol' in name:
                continue
            urls = list(asset.get('mirrors') or [])
            if asset.get('browser_download_url'):
                urls.append(asset['browser_download_url'])
            if urls:
                return urls, name, int(asset.get('size') or 0)
        logger.warning(f'未找到适用于 {tag} 的 MAA 安装包')
        return [], None, 0

    def _download(self, urls, file, size=0) -> bool:
        """
        依次尝试各镜像下载到 file，全部失败返回 False。
        """
        os.makedirs(os.path.dirname(file), exist_ok=True)
        for url in urls:
            logger.info(f'开始下载 MAA 安装包: {url}')
            try:
                with requests.get(url, headers=self.Headers, stream=True,
                                  timeout=self.Timeout) as response:
                    response.raise_for_status()
                    total = int(response.headers.get('Content-Length', 0)) or size
                    received = 0
                    last_report = 0
                    with open(file, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            f.write(chunk)
                            received += len(chunk)
                            if total:
                                percent = received * 100 // total
                                if percent >= last_report + 10:
                                    last_report = percent // 10 * 10
                                    logger.info(
                                        f'下载中 {last_report}% '
                                        f'({received // 1048576}/{total // 1048576} MB)')
                if total and os.path.getsize(file) != total:
                    logger.warning('MAA 安装包大小不一致，换下一个下载源')
                    continue
                logger.info('MAA 安装包下载完成')
                return True
            except Exception as e:
                logger.warning(f'下载失败 {url}: {e}')
                if os.path.exists(file):
                    try:
                        os.remove(file)
                    except OSError:
                        pass
        return False

    def _remove_stale_resource_files(self, package_files) -> int:
        """
        删除完整包整体提供的资源目录中、新版本已不再包含的文件和空目录。

        只处理 PackageManagedDirs 匹配且位于安装目录内的目录，
        用户自定义基建配置等其他文件不受影响。
        """
        package_paths = set()
        case_variants = {}
        managed_dirs = set()
        for name in package_files:
            name = name.replace('\\', '/')
            while name.startswith('./'):
                name = name[2:]
            name = name.rstrip('/')
            if not name:
                continue
            package_paths.add(name)
            case_variants.setdefault(name.lower(), []).append(name)
            matched = self.PackageManagedDirs.match(name)
            if matched:
                managed_dirs.add(matched.group(0))

        def in_package(rel_path, full_path):
            if rel_path in package_paths:
                return True
            # 大小写不敏感的文件系统上，仅大小写不同的路径就是新包中的同一个文件
            for variant in case_variants.get(rel_path.lower(), []):
                try:
                    if os.path.samefile(full_path, os.path.join(self.path, *variant.split('/'))):
                        return True
                except OSError:
                    pass
            return False

        real_path = os.path.realpath(self.path)
        removed = 0
        for managed_dir in sorted(managed_dirs):
            root = os.path.join(self.path, *managed_dir.split('/'))
            try:
                inside = os.path.commonpath([real_path, os.path.realpath(root)]) == real_path
            except ValueError:
                inside = False
            if not inside:
                logger.info(f'{managed_dir} 指向安装目录之外，跳过清理')
                continue
            for dirpath, _, filenames in os.walk(root, topdown=False):
                for filename in filenames:
                    full_path = os.path.join(dirpath, filename)
                    rel_path = os.path.relpath(full_path, self.path).replace(os.sep, '/')
                    if in_package(rel_path, full_path):
                        continue
                    try:
                        os.remove(full_path)
                        removed += 1
                    except OSError as e:
                        logger.warning(f'删除旧资源文件失败 {rel_path}: {e}')
                if dirpath == root:
                    continue
                rel_dir = os.path.relpath(dirpath, self.path).replace(os.sep, '/')
                try:
                    if not os.listdir(dirpath) and not in_package(rel_dir, dirpath):
                        os.rmdir(dirpath)
                except OSError as e:
                    logger.warning(f'删除旧资源目录失败 {rel_dir}: {e}')
        return removed

    @classmethod
    def _is_user_data(cls, name) -> bool:
        """安装包内的路径是否属于用户数据（配置、日志、缓存），这些不能被覆盖。"""
        name = name.replace('\\', '/')
        while name.startswith('./'):
            name = name[2:]
        name = name.lstrip('/')
        if '..' in name.split('/'):
            return True
        return any(name.startswith(prefix) for prefix in cls.UserDataDirs)

    def _extract(self, file) -> str:
        """
        解压安装包到安装目录，并清理新版本已删除的旧资源文件。

        Returns:
            str: 'ok' 成功 / 'broken' 安装包损坏或不支持 / 'locked' 文件被占用
        """
        package_files = []
        try:
            if file.endswith('.zip'):
                with zipfile.ZipFile(file, 'r') as z:
                    package_files = z.namelist()
                    for name in package_files:
                        if self._is_user_data(name):
                            continue
                        z.extract(name, self.path)
            elif file.endswith(('.tar.gz', '.tgz', '.gz')):
                with tarfile.open(file, 'r:gz') as t:
                    package_files = t.getnames()
                    members = [m for m in t.getmembers() if not self._is_user_data(m.name)]
                    t.extractall(self.path, members=members)
            else:
                logger.warning(f'不支持的安装包格式: {file}')
                return 'broken'
        except (zipfile.BadZipFile, tarfile.TarError) as e:
            logger.warning(f'MAA 安装包损坏: {e}')
            return 'broken'
        except PermissionError as e:
            logger.warning(f'MAA 文件被占用，无法覆盖: {e}')
            return 'locked'
        except OSError as e:
            logger.warning(f'MAA 安装包解压失败: {e}')
            return 'broken'

        removed = self._remove_stale_resource_files(package_files)
        if removed:
            logger.info(f'已清理 {removed} 个旧版本残留的资源文件')
        return 'ok'

    def _remove_pending(self, file=None):
        for path in (file, self.pending_json):
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _write_pending(self, file, version):
        try:
            self._write_json(self.pending_json, {
                'zip': os.path.basename(file),
                'version': version,
            })
        except OSError as e:
            logger.warning(f'写入 MAA 更新标记失败: {e}')

    def _read_pending(self) -> Optional[str]:
        data = self._read_json(self.pending_json, None)
        if not isinstance(data, dict):
            return None
        file = os.path.join(self.update_dir, data.get('zip', ''))
        if os.path.isfile(file):
            return file
        return None

    def has_pending(self) -> bool:
        """
        Returns:
            bool: 是否有下载完成、等待安装的更新包
        """
        return self._read_pending() is not None

    def apply_pending(self) -> bool:
        """
        安装上次下载好但未能安装的更新包。
        需要在 MaaCore 加载之前调用（新进程启动时）。
        """
        file = self._read_pending()
        if file is None:
            if os.path.isfile(self.pending_json):
                try:
                    os.remove(self.pending_json)
                except OSError:
                    pass
            return False

        logger.hr('MAA 安装待处理的更新包', level=2)
        result = self._extract(file)
        if result == 'ok':
            self._remove_pending(file)
            logger.info('MAA 已更新至新版本')
            return True
        if result == 'locked':
            logger.warning('MAA 文件被占用，安装失败，下次启动时会重试')
            return False

        # 安装包损坏，删掉重新下载
        logger.warning('MAA 更新包已损坏，已删除，下次启动时会重新下载')
        self._remove_pending(file)
        return False

    def check_package_update(self, apply_now=True) -> Tuple[Optional[str], Optional[str]]:
        """
        检查并更新 MAA 本体。

        Args:
            apply_now (bool): MaaCore 尚未加载时立即安装；否则只下载，等下次启动安装

        Returns:
            (当前版本, 最新版本)
        """
        current = self.get_current_version()
        latest, detail = self.get_latest_version()
        self.latest_version = latest
        self.latest_detail = detail
        if not latest:
            return current, None
        if not current:
            logger.warning('无法读取当前 MAA 版本，跳过本体更新')
            return current, latest

        logger.info(f'MAA 当前版本：{current}，最新版本：{latest}')
        if current == latest:
            logger.info('MAA 已是最新版本')
            return current, latest

        urls, name, size = self.get_download_urls(detail)
        if not urls:
            return current, latest

        file = os.path.join(self.update_dir, name)
        if os.path.isfile(file) and size and os.path.getsize(file) == size:
            logger.info(f'已存在下载好的 MAA 安装包：{name}')
        elif not self._download(urls, file, size):
            logger.warning('MAA 安装包下载失败，本次继续使用当前版本')
            return current, latest

        if apply_now:
            result = self._extract(file)
            if result == 'ok':
                self._remove_pending(file)
                logger.info(f'MAA 已更新至 {latest}')
                return current, latest
            if result == 'broken':
                self._remove_pending(file)
                logger.warning('MAA 更新包损坏，已删除')
                return current, latest

        self._write_pending(file, latest)
        logger.warning(
            f'MAA {latest} 已下载，但当前 MaaCore 正在使用中无法覆盖，'
            f'将在下次启动 Alas 时自动安装')
        return current, latest

    def clean_update_cache(self):
        """
        清理下载缓存中的临时文件。
        """
        if not os.path.isdir(self.update_dir):
            return
        keep = self._read_pending()
        for file in os.listdir(self.update_dir):
            path = os.path.join(self.update_dir, file)
            if path == keep or file == 'pending.json':
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except OSError:
                pass

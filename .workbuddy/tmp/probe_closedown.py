# -*- coding: utf-8 -*-
"""实测 MaaCore 是否接受 CloseDown 任务（不启动模拟器、不加载客户端）。"""
import ctypes
import json
import os
import sys

MAA_DIR = r"D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/Maa"
os.chdir(MAA_DIR)

# 先预加载 VC 运行时，否则 WinError 1114
sysdir = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
for name in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'concrt140.dll'):
    path = os.path.join(sysdir, name)
    if os.path.exists(path):
        try:
            ctypes.CDLL(path)
        except OSError as e:
            print('preload fail', name, e)

core = ctypes.CDLL(os.path.join(MAA_DIR, 'MaaCore.dll'))
core.AsstCreate.restype = ctypes.c_void_p
core.AsstDestroy.argtypes = [ctypes.c_void_p]
core.AsstLoadResource.argtypes = [ctypes.c_char_p]
core.AsstLoadResource.restype = ctypes.c_bool
core.AsstAppendTask.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
core.AsstAppendTask.restype = ctypes.c_int
core.AsstGetVersion.restype = ctypes.c_char_p
core.AsstSetUserDir.argtypes = [ctypes.c_char_p]
core.AsstSetUserDir.restype = ctypes.c_bool

print('MaaCore version:', core.AsstGetVersion())
print('AsstSetUserDir:', core.AsstSetUserDir(MAA_DIR.encode('utf-8')))
print('AsstLoadResource:', core.AsstLoadResource(MAA_DIR.encode('utf-8')))

handle = core.AsstCreate()
if not handle:
    print('AsstCreate failed，尝试 AsstCreateEx')
    CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)
    def _cb(code, msg, arg):
        pass
    cb = CB(_cb)
    handle = core.AsstCreateEx(cb, None)
    print('AsstCreateEx handle:', handle)
if not handle:
    print('AsstCreate failed')
    raise SystemExit(1)

cases = [
    ('CloseDown + client_type=Official', 'CloseDown', {'client_type': 'Official'}),
    ('CloseDown + client_type=Bilibili', 'CloseDown', {'client_type': 'Bilibili'}),
    ('CloseDown 无参数', 'CloseDown', {}),
    ('StartUp + start_game_enabled', 'StartUp', {'start_game_enabled': True, 'client_type': 'Official'}),
]

for title, name, params in cases:
    tid = core.AsstAppendTask(handle, name.encode('utf-8'), json.dumps(params).encode('utf-8'))
    print('%-36s -> task_id=%d %s' % (title, tid, 'OK' if tid > 0 else 'REJECTED'))

core.AsstDestroy(handle)

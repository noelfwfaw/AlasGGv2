# -*- coding: utf-8 -*-
"""离线验证 get_next_task 的收尾分支（不启动 MAA）。"""
from datetime import datetime, timedelta

from cached_property import cached_property
import submodule.AlasMaaBridge.maa as maa


class Task:
    def __init__(self, command, next_run):
        self.command = command
        self.next_run = next_run


class FakeConfig:
    def __init__(self, tasks):
        self.queue = list(tasks)
        self.task = None
        self.calls = []
        self.data = {}

    def get_next(self):
        return self.queue[0]

    def bind(self, task):
        self.task = task

    def task_call(self, name):
        self.calls.append(name)
        self.data['startup_called'] = True
        self.queue.insert(0, Task(name, datetime.now()))


class Dummy:
    @cached_property
    def config(self):
        return self._cfg

    def __init__(self, config, closed=False):
        self._cfg = config
        self.is_first_task = True
        self.closed = closed
        self.waits = []
        self.device = type('D', (), {'release_during_wait': lambda s: None})()

    def maa_close_game_during_wait(self):
        return self.closed

    def wait_until(self, future):
        self.waits.append(future)
        return True


def run(title, tasks, closed):
    config = FakeConfig(tasks)
    dummy = Dummy(config, closed)
    command = maa.ArknightsAutoScript.get_next_task(dummy)
    print('== %s -> %s' % (title, command))
    print('   task_call: %s, waits: %d, is_first_task=%s' % (config.calls, len(dummy.waits), dummy.is_first_task))


now = datetime.now()
future = now + timedelta(hours=3)

# 1. 队列清空 + 关闭游戏：应先等待，再补一次开始唤醒
run('close_game, 等到 3 小时后', [Task('MaaFight', future)], True)
# 2. 队列清空 + 主页面待机：只等待，不插任务
run('stay_there, 等到 3 小时后', [Task('MaaFight', future)], False)
# 3. 有任务到期：不能等待
run('到点直接跑', [Task('MaaFight', now - timedelta(minutes=1))], True)

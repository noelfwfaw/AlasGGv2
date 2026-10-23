import os
from datetime import datetime

from cached_property import cached_property

from module.base.timer import timer
from module.config import config_updater
from module.config.deep import deep_get, deep_iter, deep_set
from module.config.utils import *


MOD_NAME = 'maa'


def filepath_mod_argument(filename):
    """
    本子模块（AlasMaaBridge）的 argument 目录文件，区别于 Alas 主模块的
    ./module/config/argument/。

    Args:
        filename (str): argument / task / default / override / gui / dashboard

    Returns:
        str: 文件路径
    """
    return os.path.join(
        get_mod_filepath(MOD_NAME), 'module', 'config', 'argument', f'{filename}.yaml')


def filepath_mod_code():
    """
    Returns:
        str: 本模块的 config_generated.py 路径
    """
    return os.path.join(get_mod_filepath(MOD_NAME), 'module', 'config', 'config_generated.py')


class ConfigGenerator(config_updater.ConfigGenerator):
    """
    生成 MAA 子模块自己的 args.json / menu.json / config_generated.py / i18n。

    基类的 generate() 固定读写的都是 Alas 主模块的路径，直接复用会覆盖 Alas 的配置，
    因此这里把读写路径全部指向 submodule/AlasMaaBridge。
    """

    @cached_property
    def argument(self):
        """
        读取本模块的 argument.yaml，结构与基类一致。
        """
        data = {}
        raw = read_file(filepath_mod_argument('argument'))
        for path, value in deep_iter(raw, depth=2):
            arg = {
                'type': 'input',
                'value': '',
            }
            if not isinstance(value, dict):
                value = {'value': value}
            arg['type'] = data_to_type(value, arg=path[1])
            if isinstance(value['value'], datetime):
                arg['type'] = 'datetime'
                arg['validate'] = 'datetime'
            # Manual definition has the highest priority
            arg.update(value)
            deep_set(data, keys=path, value=arg)

        # Define storage group
        arg = {
            'type': 'storage',
            'value': {},
            'valuetype': 'ignore',
            'display': 'disabled',
        }
        deep_set(data, keys=['Storage', 'Storage'], value=arg)
        return data

    @cached_property
    def task(self):
        return read_file(filepath_mod_argument('task'))

    @cached_property
    def default(self):
        return read_file(filepath_mod_argument('default'))

    @cached_property
    def override(self):
        return read_file(filepath_mod_argument('override'))

    @cached_property
    def gui(self):
        return read_file(filepath_mod_argument('gui'))

    @cached_property
    def dashboard(self):
        return {'Dashboard': []}

    @cached_property
    def args(self):
        """
        基类会为 Dashboard 注入 Storage 分组，MAA 插件没有 Dashboard，
        生成时去掉以免在 template 中留下空分组。
        """
        data = super().args
        data.pop('Dashboard', None)
        return data

    @timer
    def generate(self):
        write_file(filepath_args(mod_name=MOD_NAME), self.args)
        write_file(filepath_args('menu', mod_name=MOD_NAME), self.menu)
        self.generate_code()
        for lang in LANGUAGES:
            self.generate_i18n(lang)

    def generate_code(self):
        """
        args.json ---> config_generated.py（本模块自己的）
        """
        visited_group = set()
        lines = list(config_updater.CONFIG_IMPORT)
        for path, data in deep_iter(self.argument, depth=2):
            group, arg = path
            if group not in visited_group:
                lines.append('')
                lines.append(f'    # Group `{group}`')
                visited_group.add(group)

            option = ''
            if 'option' in data and data['option']:
                option = '  # ' + ', '.join([str(opt) for opt in data['option']])
            path = '.'.join(path)
            lines.append(f'    {path_to_arg(path)} = {repr(parse_value(data["value"], data=data))}{option}')

        with open(filepath_mod_code(), 'w', encoding='utf-8', newline='') as f:
            for text in lines:
                f.write(text + '\n')

    @timer
    def generate_i18n(self, lang):
        """
        Load old translations and generate new translation file.

                     args.json ---+-----> i18n/<lang>.json
        (old) i18n/<lang>.json ---+
        """
        new = {}
        old = read_file(filepath_i18n(lang, MOD_NAME))

        def deep_load(keys, default=True, words=('name', 'help')):
            for word in words:
                k = keys + [str(word)]
                d = ".".join(k) if default else str(word)
                v = deep_get(old, keys=k, default=d)
                deep_set(new, keys=k, value=v)

        # Menu
        for path, data in deep_iter(self.task, depth=3):
            if 'tasks' not in path:
                continue
            task_group, _, task = path
            deep_load(['Menu', task_group])
            deep_load(['Task', task])
        # Arguments
        visited_group = set()
        for path, data in deep_iter(self.argument, depth=2):
            if path[0] not in visited_group:
                deep_load([path[0], '_info'])
                visited_group.add(path[0])
            deep_load(path)
            if 'option' in data:
                deep_load(path, words=data['option'], default=False)

        # GUI i18n
        for path, _ in deep_iter(self.gui, depth=2):
            group, key = path
            deep_load(keys=['Gui', group], words=(key,))

        # Copy stage names from MaaFight to MaaFightWeekly
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        for stage, trans in deep_get(new, keys='MaaFight.Stage', default={}).items():
            if '-' not in stage:
                continue
            for day in day_names:
                if deep_get(new, keys=['MaaFightWeekly', day, stage]):
                    deep_set(new, keys=['MaaFightWeekly', day, stage], value=trans)

        write_file(filepath_i18n(lang, MOD_NAME), new)


class ConfigUpdater(config_updater.ConfigUpdater):
    redirection = []

    @cached_property
    def args(self):
        return read_file(filepath_args(mod_name=MOD_NAME))

    def read_file(self, config_name, is_template=False):
        old = read_file(filepath_config(config_name, MOD_NAME))
        return self.config_update(old, is_template=is_template)

    @staticmethod
    def write_file(config_name, data, mod_name=MOD_NAME):
        write_file(filepath_config(config_name, mod_name), data)

    def config_update(self, old, is_template=False):
        """
        Args:
            old (dict):
            is_template (bool):

        Returns:
            dict:
        """
        new = {}

        for keys, data in deep_iter(self.args, depth=3):
            value = deep_get(old, keys=keys, default=data['value'])
            if is_template or value is None or value == '' or data['type'] == 'lock' or data.get('display') == 'hide':
                value = data['value']
            value = parse_value(value, data=data)
            deep_set(new, keys=keys, value=value)

        if not is_template:
            new = self.config_redirect(old, new)

        return new


if __name__ == '__main__':
    """
    Process the whole config generation.

                 task.yaml -+----------------> menu.json
             argument.yaml -+-> args.json ---> config_generated.py
             override.yaml -+       |
                  gui.yaml --------\|
                                   ||
    (old) i18n/<lang>.json --------\\========> i18n/<lang>.json
    (old)    template.json ---------\========> template.json
    """
    # Ensure running in project root
    if not os.path.isdir('./submodule'):
        os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../../..'))
    ConfigGenerator().generate()
    ConfigUpdater().update_file('template', is_template=True)

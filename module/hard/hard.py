import importlib
from datetime import datetime

from campaign.campaign_hard.campaign_hard import Campaign
from module.campaign.run import CampaignRun
from module.config.utils import server_time_offset
from module.handler.fast_forward import to_map_file_name
from module.hard.assets import *
from module.logger import logger
from module.ocr.ocr import Digit

OCR_HARD_REMAIN = Digit(OCR_HARD_REMAIN, letter=(123, 227, 66), threshold=128, alphabet='0123')


class CampaignHard(CampaignRun):
    equipment_has_take_on = False
    campaign: Campaign

    # 每日困难关卡按星期配置，索引与 datetime.weekday() 对应（0=周一）
    WEEKDAY_STAGES = [
        'Hard_HardStageMonday',
        'Hard_HardStageTuesday',
        'Hard_HardStageWednesday',
        'Hard_HardStageThursday',
        'Hard_HardStageFriday',
        'Hard_HardStageSaturday',
        'Hard_HardStageSunday',
    ]
    WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    def get_stage(self):
        """
        按服务器时间取今天星期几对应的困难关卡。

        Returns:
            str: 今天的困难关卡，如 '11-4'
        """
        weekday = (datetime.now() - server_time_offset()).weekday()
        stage = getattr(self.config, self.WEEKDAY_STAGES[weekday])
        logger.info(f'Today is {self.WEEKDAY_NAMES[weekday]}, hard stage: {stage}')
        return stage

    def run(self):
        logger.hr('Campaign hard', level=1)
        stage = self.get_stage()
        name = to_map_file_name(stage)
        self.config.override(
            Campaign_Mode='hard',
            Campaign_UseFleetLock=True,
            Campaign_UseAutoSearch=True,
            Fleet_FleetOrder='fleet1_all_fleet2_standby' if self.config.Hard_HardFleet == 1 else 'fleet1_standby_fleet2_all',
            Emotion_Mode='nothing',  # Dont calculate and dont ignore
        )
        # Equipment take on
        # campaign/campaign_hard/campaign_hard.py Campaign.fleet_preparation()

        # Initial
        self.load_campaign(name='campaign_hard', folder='campaign_hard')  # Load campaign file
        module = importlib.import_module('.' + name, 'campaign.campaign_main')  # Load map from normal mode.
        self.campaign.MAP = module.MAP

        # UI ensure
        self.device.screenshot()
        self.campaign.device.image = self.device.image
        self.campaign.ensure_campaign_ui(
            name=stage,
            mode='hard'
        )

        # Run
        remain = OCR_HARD_REMAIN.ocr(self.device.image)
        logger.attr('Remain', remain)
        for n in range(remain):
            self.campaign.run()

        self.campaign.ensure_auto_search_exit()
        # self.campaign.equipment_take_off_when_finished()

        # Scheduler
        self.config.task_delay(server_update=True)
        self.config.task_call('Reward')

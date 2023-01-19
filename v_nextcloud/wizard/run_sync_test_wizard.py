# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import fields, models

class RunSyncTestWizard(models.TransientModel):
    _name = 'run.sync.test.wizard'
    
    message = fields.Text()
    
    def run_sync_cron_test(self):
        test_sync_id = self.env['nextcloud.sync'].search([], limit=1)
        if test_sync_id:
            test_sync_id.sync_cron_test()
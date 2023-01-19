# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields


class NcSyncLog(models.Model):
    _name = 'nc.sync.log'

    name = fields.Char()
    description = fields.Char()
    date_start = fields.Datetime()
    date_end = fields.Datetime()
    state = fields.Selection([('connecting', 'Connecting'),
                              ('in_progress', 'In Progress'),
                              ('ok', 'Ok'),
                              ('failed', 'Failed'),
                              ('error', 'Error')])
    next_cloud_url = fields.Char()
    odoo_url = fields.Char()
    duration = fields.Char()
    line_ids = fields.One2many('nc.sync.log.line', 'log_id')


class NcSyncLogLine(models.Model):
    _name = 'nc.sync.log.line'

    log_id = fields.Many2one('nc.sync.log')
    operation_type = fields.Selection([('create', 'Create'),
                                       ('write', 'Write'),
                                       ('delete', 'Delete'),
                                       ('read', 'Read'),
                                       ('login', 'Login'),
                                       ('conflict', 'Conflict'),
                                       ('warning', 'Warning'),
                                       ('error', 'Error')])
    prev_value = fields.Text()
    new_value = fields.Text()
    data_send = fields.Text()
    error_code_id = fields.Many2one('nc.sync.error')
    severity = fields.Selection([('debug', 'Debug'),
                                 ('info', 'Info'),
                                 ('warning', 'Warning'),
                                 ('error', 'Error'),
                                 ('critical', 'Critical')])
    response_description = fields.Text()

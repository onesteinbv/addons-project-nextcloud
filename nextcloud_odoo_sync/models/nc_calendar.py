# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields
    
class NcsyncUser(models.Model):
    _name = 'nc.calendar'
    
    name = fields.Char()
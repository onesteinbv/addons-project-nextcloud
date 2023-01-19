# -*- coding: utf-8 -*-
# Copyright (c) 2022 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import requests
from odoo import models
from odoo.exceptions import ValidationError
from odoo.http import request


class NextcloudWebdav(models.AbstractModel):
    _name = 'nextcloud.webdav'
    _description = 'NextCloud WebDav'

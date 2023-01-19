# -*- coding: utf-8 -*-
# Copyright (c) 2022 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import requests
from odoo import models
from odoo.exceptions import ValidationError
from odoo.http import request


class NextcloudBase(models.AbstractModel):
    _name = 'nextcloud.base'
    _description = 'NextCloud Base API'

    def get_auth_data(self):
        data = {'h_get': {"OCS-APIRequest": "true"},
                'h_post': {"OCS-APIRequest": "true",
                           "Content-Type": "application/x-www-form-urlencoded"},
                'auth_pk': (self.username, self.password), }
        return data

    def get_full_url(self, additional_url="", api_url=""):
        """
        Build full url for request to NextCloud api
        Construct url from self.base_url, self.API_URL, additional_url (if given),
        add format=json param if self.json
        :param additional_url: str
            add to url after api_url
        :return: str
        """
        if additional_url and not str(additional_url).startswith("/"):
            additional_url = "/{}".format(additional_url)

        # if self.json_output:
            # self.query_components.append("format=json")

        res = "{base_url}{api_url}{additional_url}".format(
            base_url=self.hostname, api_url=api_url, additional_url=additional_url)

        if self.json_output:
            res += "?format=json"
        return res

    def rtn(self, resp):
        if self.json_output:
            return resp.json()
        else:
            return resp.content.decode("UTF-8")

    def get(self, url="", params=None):
        url = self.get_full_url(url, self.api_url)
        data = self.get_auth_data()
        res = requests.get(url, auth=data['auth_pk'], headers=data['h_get'], params=params)
        return self.rtn(res)

    def post(self, url="", params=None):
        url = self.get_full_url(url, self.api_url)
        data = self.get_auth_data()
        res = request.post(url, auth=data['auth_pk'], data=data, headers=data['h_post'])
        return self.rtn(res)

    def get_users(self, search=None, limit=None, offset=None):
        """
        Retrieve a list of users from the Nextcloud server
        :param search: string, optional search string
        :param limit: int, optional limit value
        :param offset: int, optional offset value
        :return:
        """
        params = {
            'search': search,
            'limit': limit,
            'offset': offset
        }
        result = self.get(params=params)
        if isinstance(result, dict):
            users = []
            for uid in result["ocs"]["data"]["users"]:
                url = self.get_full_url(uid, self.api_url)
                data = self.get_auth_data()
                res = requests.get(url, auth=data['auth_pk'], headers=data['h_get'], params=params)
                users.append(res.json()["ocs"]["data"])
            result["ocs"]["data"]["users"] = users
        return result

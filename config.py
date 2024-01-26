# -*- encoding: utf-8 -*-
"""
@File    : config.py
@Time    : 2021/11/20 17:17
@Author  : smy
@Email   : smyyan@foxmail.com
@Software: PyCharm
"""

import configparser


def _print_config(config):
    sections = config.sections()
    print("sections:", sections)
    for section in sections:
        print("[%s]" % section)
        for option in config.options(section):
            print("\t%s=%s" % (option, config.get(section, option)))


class ReadConfig:

    def __init__(self, filepath=None):
        if filepath:
            config_path = filepath
        else:
            config_path = "config/config.ini"

        self.cf = configparser.ConfigParser()
        self.cf.read(config_path, encoding='utf8')
        _print_config(self.cf)
        print(self.get_proxy())

    def get_bot_config(self, param):
        value = self.cf.get("ByrBTBot", param, fallback=None)
        return value

    def get_transmission_config(self, param):
        value = self.cf.get("Transmission", param, fallback=None)
        return value
    
    def get_proxy(self):
        proxy = (
            None
            if self.cf.get("Proxy", 'proxy-enable') != "1"
            else {
                "https": self.cf.get("Proxy", 'proxy-type')
                + "://"
                + self.cf.get("Proxy", 'proxy-username')
                + ":"
                + self.cf.get("Proxy", 'proxy-password')
                + "@"
                + self.cf.get("Proxy", 'proxy-host')
                + ":"
                + self.cf.get("Proxy", 'proxy-port'),
            }
        )
        return proxy


if __name__ == '__main__':
    test = ReadConfig()
    t = test.get_bot_config("byrbt-url")
    print(t)

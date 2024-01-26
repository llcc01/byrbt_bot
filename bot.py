# -*- encoding: utf-8 -*-
"""
@File    : bot.py
@Time    : 2021/11/20 17:15
@Author  : smy
@Email   : smyyan@foxmail.com
@Software: PyCharm
"""

from config import ReadConfig
from login import LoginTool

import signal
import sys
import os
import pickle
import re
import time
import requests
from contextlib import ContextDecorator
from requests.cookies import RequestsCookieJar
from bs4 import BeautifulSoup
from transmission_rpc import Torrent

from utils.bit_torrent_utils import BitTorrent


def _handle_interrupt(signum, frame):
    sys.exit()  # will trigger a exception, causing __exit__ to be called


class TorrentBot(ContextDecorator):
    def __init__(self, config: ReadConfig, login: LoginTool, torrent_util: BitTorrent):
        super(TorrentBot, self).__init__()
        self.config = config
        self.login = login
        self.torrent_util = torrent_util
        self.base_url = str(config.get_bot_config("byrbt-url"))
        self.torrent_url = self._get_url('torrents.php')
        self.cookie_jar = RequestsCookieJar()
        self.byrbt_cookies = login.load_cookie()
        if self.byrbt_cookies is not None:
            for k, v in self.byrbt_cookies.items():
                self.cookie_jar[k] = v

        self.old_torrent = list()
        self.torrent_download_record_save_path = './data/torrent.pkl'
        self.max_torrent_count = int(config.get_bot_config("max-torrent"))
        # all size in Byte
        self.max_torrent_total_size = int(config.get_bot_config("max-torrent-total-size"))
        if self.max_torrent_total_size is None or self.max_torrent_total_size < 0:
            self.max_torrent_total_size = 0
        self.max_torrent_total_size = self.max_torrent_total_size * 1024 * 1024 * 1024
        self.torrent_max_size = int(config.get_bot_config("torrent-max-size"))
        if self.torrent_max_size is None or self.torrent_max_size > 1024:
            print("torrent-max-size wrong setting, Use default setting: torrent-max-size: 1024G")
            self.torrent_max_size = 1024
        self.torrent_max_size = self.torrent_max_size * 1024 * 1024 * 1024
        self.torrent_min_size = int(config.get_bot_config("torrent-min-size"))
        if self.torrent_min_size is None or self.torrent_min_size < 1:
            print("torrent-min-size wrong setting, Use default setting: torrent-min-size: 1G")
            self.torrent_min_size = 1
        self.torrent_min_size = self.torrent_min_size * 1024 * 1024 * 1024
        if self.torrent_min_size > self.torrent_max_size:
            print("torrent-min-size is greater than torrent-max-size, please check config.ini! Use default setting: "
                  "torrent-max-size: 1024G, torrent-min-size: 1G")
            self.torrent_max_size = 1024 * 1024 * 1024 * 1024
            self.torrent_min_size = 1 * 1024 * 1024 * 1024

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.113 Safari/537.36'}
        self._filter_tags = ['免费', '免费&2x上传']
        self._tag_map = {
            # highlight & tag
            'free': '免费',
            'twoup': '2x上传',
            'twoupfree': '免费&2x上传',
            'halfdown': '50%下载',
            'twouphalfdown': '50%下载&2x上传',
            'thirtypercentdown': '30%下载',
            # icon
            '2up': '2x上传',
            'free2up': '免费&2x上传',
            '50pctdown': '50%下载',
            '50pctdown2up': '50%下载&2x上传',
            '30pctdown': '30%下载',
        }
        self._cat_map = {
            '电影': 'movie',
            '剧集': 'episode',
            '动漫': 'anime',
            '音乐': 'music',
            '综艺': 'show',
            '游戏': 'game',
            '软件': 'software',
            '资料': 'material',
            '体育': 'sport',
            '记录': 'documentary',
        }

    def __enter__(self):
        print('启动byrbt_bot!')
        time.sleep(5)  # wait transmission process
        signal.signal(signal.SIGINT, _handle_interrupt)
        signal.signal(signal.SIGTERM, _handle_interrupt)
        os.makedirs(os.path.dirname(self.torrent_download_record_save_path), mode=0o755, exist_ok=True)
        if os.path.exists(self.torrent_download_record_save_path):
            self.old_torrent = pickle.load(open(self.torrent_download_record_save_path, 'rb'))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print('退出')
        print('保存数据')
        pickle.dump(self.old_torrent, open(self.torrent_download_record_save_path, 'wb'), protocol=2)

    def _get_url(self, url):
        return self.base_url + url

    def _get_tag(self, tag):
        try:
            if tag == '':
                return ''
            else:
                tag = tag.split('_')[0]

            return self._tag_map[tag]
        except KeyError:
            return ''

    def get_user_info(self, user_info_block):
        print("user info:")
        try:
            user_name = user_info_block.select_one('.nowrap').text
            user_info_text = user_info_block.text
            index_s = user_info_text.find('等级')
            index_e = user_info_text.find('当前活动')
            if index_s == -1 or index_e == -1:
                print("[]")
                return
            user_info_text = user_info_text[index_s:index_e]
            user_info_text = re.sub("[\xa0\n]", ' ', user_info_text)
            user_info_text = re.sub("\[[^\[]*\]", '', user_info_text).replace('：', ':')
            user_info_text = re.sub(" *: *", ':', user_info_text).strip()
            user_info_text = re.sub("\s+", ' ', user_info_text)
            user_info_text = "用户名:" + user_name + " " + user_info_text
            print(user_info_text)

        except Exception as e:
            print("user info not found!")
            print('[ERROR] ' + repr(e))

    def get_torrent_info_filter_by_tag(self, table, filter_tags):
        assert isinstance(table, list)
        torrent_infos = list()
        for item in table:
            torrent_info = dict()
            tds = item.find_all('td', recursive=False)
            # tds[0] 是 引用

            # tds[1] 是分类
            cat = tds[1].find('a').text.strip()
            
            # 主要信息的td
            main_td = tds[2].select('table > tr > td')[0]
            if main_td.find('div'):
                main_td = tds[2].select('table > tr > td')[1]
            
            # 链接
            href = main_td.select('a')[0].attrs['href']

            # 种子id
            seed_id = re.findall(r'id=(\d+)', href)[0]

            # 标题
            title = main_td.find('a').attrs['title']

            tags = set([font.attrs['class'][0] for font in main_td.select('span > span') if 'class' in font.attrs.keys()])
            if '' in tags:
                tags.remove('')

            is_seeding = len(main_td.select('img[src="/pic/seeding.png"]')) > 0
            is_finished = len(main_td.select('img[src="/pic/finished.png"]')) > 0

            is_hot = False
            if 'hot' in tags:
                is_hot = True
                tags.remove('hot')
            is_new = False
            if 'new' in tags:
                is_new = True
                tags.remove('new')
            is_recommended = False
            if 'recommended' in tags:
                is_recommended = True
                tags.remove('recommended')

            # 根据控制面板中促销种子的标记方式不同来匹配
            if 'class' in item.attrs:
                # 默认高亮方式
                tag = self._get_tag(item.attrs['class'][0])
            elif len(tags) == 1:
                # 文字标记方式
                # 不属于 hot、new、recommended 的标记即为促销标记
                tag = self._get_tag(list(tags)[0])
            elif len(main_td.select('img[src="/pic/trans.gif"][class^="pro_"]')) > 0:
                # 添加图标方式
                tag = self._get_tag(main_td.select('img[src="/pic/trans.gif"][class^="pro_"]')[-1].attrs['class'][0].split('_')[-1])
            else:
                tag = ''

            file_size = tds[5].text.split('\n')

            seeding = int(tds[6].text) if tds[6].text.isdigit() else -1

            downloading = int(tds[7].text) if tds[7].text.isdigit() else -1

            finished = int(tds[8].text) if tds[8].text.isdigit() else -1

            torrent_info['cat'] = cat
            torrent_info['is_hot'] = is_hot
            torrent_info['tag'] = tag
            torrent_info['is_seeding'] = is_seeding
            torrent_info['is_finished'] = is_finished
            torrent_info['seed_id'] = seed_id
            torrent_info['title'] = title
            torrent_info['seeding'] = seeding
            torrent_info['downloading'] = downloading
            torrent_info['finished'] = finished
            torrent_info['file_size'] = file_size
            torrent_info['is_new'] = is_new
            torrent_info['is_recommended'] = is_recommended
            torrent_infos.append(torrent_info)

        torrent_infos_filter_by_tag = list()
        for torrent_info in torrent_infos:
            if torrent_info['tag'] in filter_tags:
                torrent_infos_filter_by_tag.append(torrent_info)

        return torrent_infos_filter_by_tag

    # 获取可用的种子的策略，可自行修改
    def get_ok_torrent(self, torrent_infos):
        ok_infos = list()
        if len(torrent_infos) >= 20:
            # 遇到free或者免费种子太过了，择优选取，标准是(下载数/上传数)>20，并且文件大小大于20GB
            print('符合要求的种子过多，可能开启Free活动了，提高种子获取标准')
            for torrent_info in torrent_infos:
                if torrent_info['seed_id'] in self.old_torrent:
                    continue
                # 下载1GB-1TB之间的种子（下载以GB大小结尾的种子，脚本需要不可修改）
                if 'GB' not in torrent_info['file_size'][0]:
                    continue
                if torrent_info['seeding'] <= 0 or torrent_info['downloading'] < 0:
                    continue
                if torrent_info['seeding'] != 0 and float(torrent_info['downloading']) / float(
                        torrent_info['seeding']) < 20:
                    continue
                file_size = torrent_info['file_size'][0]
                file_size = file_size.replace('GB', '')
                file_size = float(file_size.strip())
                if file_size < 20.0:
                    continue
                ok_infos.append(torrent_info)
        else:
            # 正常种子选择标准是免费种子并且(下载数/上传数)>0.6
            for torrent_info in torrent_infos:
                if torrent_info['seed_id'] in self.old_torrent:
                    continue
                # 下载1GB-1TB之间的种子（下载以GB大小结尾的种子，脚本需要不可修改）
                if 'GiB' not in torrent_info['file_size'][0]:
                    continue
                if torrent_info['seeding'] <= 0 or torrent_info['downloading'] < 0:
                    continue
                if torrent_info['seeding'] != 0 and float(torrent_info['downloading']) / float(
                        torrent_info['seeding']) < 0.6:
                    continue
                ok_infos.append(torrent_info)
        return ok_infos

    def check_remove(self, add_num=0):
        torrent_list = self.torrent_util.get_list()
        if torrent_list is None:
            print('get torrent list fail!')
            return

        torrent_len = len(torrent_list) + add_num
        if torrent_len <= self.max_torrent_count:
            return
        torrent_list.sort(key=lambda x: (x.activity_date, x.rate_upload))
        while torrent_len > self.max_torrent_count and len(torrent_list) > 0:
            remove_torrent_info = torrent_list.pop(0)
            if remove_torrent_info.status.checking:
                continue
            # rateUpload > 500KB/s
            if (remove_torrent_info.status.downloading or remove_torrent_info.status.seeding) and \
                    remove_torrent_info.rate_upload > 500000:
                continue

            if remove_torrent_info.download_dir == bit_torrent.download_path:
                res = self.torrent_util.remove(remove_torrent_info.id, delete_data=True)
                if res:
                    print('remove torrent success: ' + str(remove_torrent_info))
                else:
                    print('remove torrent fail: ' + str(remove_torrent_info))

            torrent_len = torrent_len - 1

    def download(self, torrent_id):
        download_url = 'download.php?id={}'.format(torrent_id)
        download_url = self._get_url(download_url)
        flag = False
        r = None
        for _ in range(5):
            try:
                r = requests.get(download_url, cookies=self.cookie_jar, headers=self.headers)
                flag = True
                break
            except Exception as e:
                print('[ERROR] ' + repr(e))
                print('try login...')
                self.byrbt_cookies = self.login.load_cookie()
                if self.byrbt_cookies is not None:
                    self.cookie_jar = RequestsCookieJar()
                    for k, v in self.byrbt_cookies.items():
                        self.cookie_jar[k] = v
                time.sleep(1)

        if flag is False or r is None:
            print('login failed!')

        new_torrent = self.torrent_util.download_from_content(r.content, paused=True)
        if new_torrent is not None:
            new_torrent_size = new_torrent.total_size
            if new_torrent_size < self.torrent_min_size or new_torrent_size > self.torrent_max_size:
                print('add new torrent fail, name : {}, improper seed size: {} GB, download url: {}'.format(
                    new_torrent.name, new_torrent_size / 1000000000, download_url))
                self.old_torrent.append(torrent_id)
                self.torrent_util.remove(new_torrent.id, delete_data=True)
                return False
            res = self.check_free_space_to_download(new_torrent_size)
            if res is None:
                self.torrent_util.remove(new_torrent.id, delete_data=True)
                return False
            if res is False:
                self.torrent_util.remove(new_torrent.id, delete_data=True)
                print('add new torrent fail, not device space to download, name : {}, size: {} GB, '
                      'download url: {}'.format(new_torrent.name, new_torrent_size / 1000000000, download_url))
                return False
            else:
                if self.torrent_util.start_torrent(new_torrent.id):
                    print('add torrent: ' + str(res))
                else:
                    print('add new torrent fail, start torrent fail, name : {}, seed size: {} GB, '
                          'download url: {}'.format(new_torrent.name, new_torrent_size / 1000000000, download_url))
                self.old_torrent.append(torrent_id)
                return True
        else:
            print('add new torrent fail, download url: ' + download_url)
            self.old_torrent.append(torrent_id)
            return False

    def start(self):
        scan_interval_in_sec = 60
        check_disk_space_interval_in_sec = 500
        last_check_disk_space_time = -1
        while True:
            now_time = int(time.time())
            if now_time - last_check_disk_space_time > check_disk_space_interval_in_sec:
                print('check disk space...')
                if self.check_disk_space():
                    last_check_disk_space_time = now_time
                else:
                    print('check disk space fail!')
                    time.sleep(scan_interval_in_sec)
                    continue

            print('scan torrent list...')
            flag = False
            torrents_soup = None
            torrent_infos = None
            try:
                torrents_soup = BeautifulSoup(
                    requests.get(self.torrent_url, cookies=self.cookie_jar, headers=self.headers).content,
                    features="lxml")
                flag = True
            except Exception as e:
                print('[ERROR] ' + repr(e))
                self.byrbt_cookies = self.login.load_cookie()
                if self.byrbt_cookies is not None:
                    self.cookie_jar = RequestsCookieJar()
                    for k, v in self.byrbt_cookies.items():
                        self.cookie_jar[k] = v

            if flag is False:
                print('login failed!')
                break

            try:
                user_info_block = torrents_soup.select_one('#info_block').select_one('.bottom')
                self.get_user_info(user_info_block)
            except Exception as e:
                print('[ERROR] ' + repr(e))

            try:
                torrent_table = torrents_soup.select('.torrents > tr')[1:]
                torrent_infos = self.get_torrent_info_filter_by_tag(torrent_table, self._filter_tags)
                flag = True
            except Exception as e:
                print('[ERROR] ' + repr(e))
                flag = False

            if flag is False:
                print('failed to parse torrent table!')
                break
            print('free torrent list：')
            for i, info in enumerate(torrent_infos):
                print('{} : {} {} {}'.format(i, info['seed_id'], info['file_size'], info['title']))

            ok_torrent = self.get_ok_torrent(torrent_infos)
            print('available torrent list：')
            for i, info in enumerate(ok_torrent):
                print('{} : {} {} {}'.format(i, info['seed_id'], info['file_size'], info['title']))
            self.check_remove(add_num=len(ok_torrent))
            for torrent in ok_torrent:
                if self.download(torrent['seed_id']) is False:
                    print('{} download fail'.format(torrent['title']))
                    continue
            time.sleep(scan_interval_in_sec)
            print()

    def check_free_space_to_download(self, new_torrent_size):
        torrent_list = self.torrent_util.get_list()
        if torrent_list is None:
            print('get torrent list fail!')
            return None
        free_space = self.torrent_util.get_free_space()
        if free_space is None:
            print('get download path free space fail!')
            return None
        sum_size = 0
        for torrent in torrent_list:
            sum_size += torrent.total_size

        if new_torrent_size < free_space and sum_size + new_torrent_size <= self.max_torrent_total_size:
            return True

        print('insufficient disk space, try to remove some torrent...')
        torrent_list.sort(key=lambda x: (x.activity_date, x.rate_upload))
        while (free_space <= new_torrent_size or sum_size + new_torrent_size > self.max_torrent_total_size) \
                and len(torrent_list) > 0:
            remove_torrent_info = torrent_list.pop(0)
            if remove_torrent_info.status.checking:
                continue
            # rateUpload > 500KB/s
            if (remove_torrent_info.status.downloading or remove_torrent_info.status.seeding) and \
                    remove_torrent_info.rate_upload > 500000:
                continue

            if remove_torrent_info.download_dir == bit_torrent.download_path:
                res = self.torrent_util.remove(remove_torrent_info.id, delete_data=True)
                if res:
                    print('remove torrent success: ' + str(remove_torrent_info))
                else:
                    print('remove torrent fail: ' + str(remove_torrent_info))
                    return None
            
            free_space += remove_torrent_info.total_size
            sum_size -= remove_torrent_info.total_size

        return self.torrent_util.get_free_space() > new_torrent_size and sum_size + new_torrent_size <= self.max_torrent_total_size

    def check_disk_space(self, threshold: int = 5*1024*1024*1024):
        free_space = self.torrent_util.get_free_space()
        if free_space is None:
            print('get download path free space fail!')
            return False

        if free_space <= threshold:  # 5GB
            print('low disk space, clear torrent...')
            torrent_list = self.torrent_util.get_list()
            if torrent_list is None:
                print('get torrent list fail!')
                return False
            torrent_list.sort(key=lambda x: (x.activity_date, x.rate_upload))
            while free_space <= threshold and len(torrent_list) > 0:
                remove_torrent_info = torrent_list.pop(0)
                if remove_torrent_info.status.checking:
                    continue
                # rateUpload > 500KB/s
                if (remove_torrent_info.status.downloading or remove_torrent_info.status.seeding) and \
                        remove_torrent_info.rate_upload > 500000:
                    continue

                if remove_torrent_info.download_dir == bit_torrent.download_path:
                    res = self.torrent_util.remove(remove_torrent_info.id, delete_data=True)
                    if res:
                        print('remove torrent success: ' + str(remove_torrent_info))
                    else:
                        print('remove torrent fail: ' + str(remove_torrent_info))
                        return False
                    
                free_space += remove_torrent_info.total_size
            return self.torrent_util.get_free_space() > threshold

        return True


if __name__ == '__main__':
    config = ReadConfig(filepath='config/config.ini')
    login = LoginTool(config)
    bit_torrent = BitTorrent(config)
    with TorrentBot(config, login, bit_torrent) as byrbt_bot:
        byrbt_bot.start()

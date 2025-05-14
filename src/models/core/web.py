"""
刮削过程的网络操作
"""

import os
import re
import shutil
import time
import traceback
import urllib
import math

from lxml import etree

from models.base.file import copy_file, delete_file, move_file, split_path
from models.base.image import check_pic, cut_thumb_to_poster
from models.base.pool import Pool
from models.base.utils import get_used_time
from models.base.web import check_url, get_amazon_data, get_big_pic_by_google, get_html, get_imgsize, multi_download
from models.config.config import config
from models.core.flags import Flags
from models.core.utils import convert_half
from models.signals import signal
from datetime import datetime

def get_actorname(number):
    # 获取真实演员名字
    url = f"https://av-wiki.net/?s={number}"
    result, res = get_html(url)
    if not result:
        return False, f"Error: {res}"
    html_detail = etree.fromstring(res, etree.HTMLParser(encoding="utf-8"))
    actor_box = html_detail.xpath('//ul[@class="post-meta clearfix"]')
    for each in actor_box:
        actor_name = each.xpath('li[@class="actress-name"]/a/text()')
        actor_number = each.xpath('li[@class="actress-name"]/following-sibling::li[last()]/text()')
        if actor_number:
            if actor_number[0].upper().endswith(number.upper()) or number.upper().endswith(actor_number[0].upper()):
                return True, ",".join(actor_name)
    return False, "No Result!"


def get_yesjav_title(json_data, movie_number):
    yesjav_url = "http://www.yesjav.info/search.asp?q=%s&" % movie_number
    movie_title = ""
    result, response = get_html(yesjav_url)
    if result and response:
        parser = etree.HTMLParser(encoding="utf-8")
        html = etree.HTML(response, parser)
        movie_title = html.xpath(
            '//dl[@id="zi"]/p/font/a/b[contains(text(), $number)]/../../a[contains(text(), "中文字幕")]/text()',
            number=movie_number,
        )
        if movie_title:
            movie_title = movie_title[0]
            for each in config.char_list:
                movie_title = movie_title.replace(each, "")
            movie_title = movie_title.strip()
    return movie_title


def google_translate(title, outline):
    e1 = None
    e2 = None
    if title:
        title, e1 = _google_translate(title)
    if outline:
        outline, e2 = _google_translate(outline)
    return title, outline, e1 or e2


def _google_translate(msg: str) -> (str, str):
    try:
        msg_unquote = urllib.parse.unquote(msg)
        url = f"https://translate.google.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q={msg_unquote}"
        result, response = get_html(url, json_data=True)
        if not result:
            return msg, f"请求失败！可能是被封了，可尝试更换代理！错误：{response}"
        return "".join([sen[0] for sen in response[0]]), ""
    except Exception as e:
        return msg, str(e)


def download_file_with_filepath(json_data, url, file_path, folder_new_path):
    if not url:
        return False

    if not os.path.exists(folder_new_path):
        os.makedirs(folder_new_path)
    try:
        if multi_download(url, file_path):
            return True
    except:
        pass
    json_data["logs"] += f"\n 🥺 Download failed! {url}"
    return False


def _mutil_extrafanart_download_thread(task):
    json_data, extrafanart_url, extrafanart_file_path, extrafanart_folder_path, extrafanart_name = task
    if download_file_with_filepath(json_data, extrafanart_url, extrafanart_file_path, extrafanart_folder_path):
        if check_pic(extrafanart_file_path):
            return True
    else:
        json_data["logs"] += f"\n 💡 {extrafanart_name} download failed! ( {extrafanart_url} )"
        return False

def _split_actor(raw_actor_list):
    """
    拆分含有括号的演员名，并返回去重后的演员列表。
    参数:
        raw_actor_list (list): 原始演员列表, 可能包含带括号的演员名
    返回:
        list: 去重后的演员列表。
    """
    # 创建一个空集合用于存储结果
    actor_list = []
    # 遍历列表
    for item in raw_actor_list:
        # 使用正则表达式匹配括号内容
        match = re.match(r"(.+?)[（\(](.+?)[）\)]", item)
        if match:  # 如果匹配成功
            name_before_bracket = match.group(1).strip()  # 括号前的内容
            name_in_bracket = match.group(2).strip()      # 括号内的内容
            
            # 将拆分后的两个部分添加到集合中
            actor_list.append(name_before_bracket)
            actor_list.append(name_in_bracket)
        else:
            # 如果没有括号，直接添加到集合中
            actor_list.append(item.strip())

    # 将集合转换为列表
    return list(dict.fromkeys(actor_list))


def _get_actor_list(json_data, title, raw_actor_list):
    """
    对含有括号的演员名进行拆分整合，返回去重演员列表, 并且将最符合的演员名置于首位。
    入参:
        json_data (dict): 刮削获得的JSON数据
        title (str): 刮削获得的原标题
        raw_actor_list (list): 刮削获得的原始演员列表
    返回:
        整合后的演员列表
    """
    print(f"原始标题: {title}")
    print(f"原始演员列表: {raw_actor_list}")
    
    raw_actor_in_title = json_data.get("amazon_orginaltitle_actor")
    print(f"原始标题中的演员: {raw_actor_in_title}")
    
    
    # 调用 _split_actor 函数处理演员列表
    actor_list = _split_actor(raw_actor_list) if raw_actor_list else []
    actor_in_title_list = _split_actor([raw_actor_in_title]) if raw_actor_in_title else []
    
    # 将标题中的演员名放在首位
    combined_actor_list = actor_in_title_list + actor_list
    
    for actor in combined_actor_list:
        # 此处的 title已经去除了末尾的演员名, 但是标题中间依然可能包含演员名, 如果匹配则放置首位
        if actor in title:
            print(f"标题中的演员名: {actor}")
            combined_actor_list.insert(0, actor)
            break
    
    # 去重并保持顺序
    actor_list = list(dict.fromkeys(combined_actor_list))
    # 选取首位最符合的演员名用以添加到标题末尾
    best_match_actor = actor_list[0] if actor_list else ""
    print(f"整合后的演员列表: {actor_list}")
    print(f"最符合的演员名: {best_match_actor}")
    return actor_list, best_match_actor

def _add_actor_to_title(title_list, best_match_actor):
    """
    将最符合的演员名添加到标题末尾, 与原标题交错插入新列表中
    """
    if best_match_actor:
        search_title_list = []
        titles_with_actor = [title + " " + best_match_actor for title in title_list]
        for title, title_with_actor in zip(title_list, titles_with_actor):
            search_title_list.append(title)
            search_title_list.append(title_with_actor)
        return search_title_list
    else:
        return title_list

def _split_title(original_title,
                 actor_list,
                 best_match_actor,
                 min_length=3,
                 pattern=None,
                 separator=" ",
                 extra_separator=None
                 ):
    """
    入参 original_title 为原始标题并且去除了末尾的演员名, 将其拆分整合用作Amazon搜索
    1. 移除标题末尾的演员名
    2. 正则匹配标题中的 pattern 并移除;
    3. 对标题进行敏感词转换，若转换后结果不同则加入结果列表;
    4. 如果标题长度不超过 min_length 则直接添加演员名;
    5. 构建分隔符正则表达式;
    6. 若原始标题不包含任何分隔符，则直接返回基础标题列表;
    7. 否则按主分隔符拆分并过滤无效子串;
    8. 若有额外分隔符，继续按其拆分;
    9. 最终返回原始标题与敏感词转换后的标题列表, 以及所有有效标题片段的去重列表。
    """
    if actor_list:
        for actor in actor_list:
            original_title = original_title.rstrip()
            if original_title.endswith(actor):
                # 移除结尾的演员名
                original_title = original_title[:-len(actor)].strip()
    if pattern:
        original_title = re.sub(pattern, "", original_title).strip()
    
    # 初始化原标题列表
    no_split_title_list = [original_title]
    
    # 敏感词转换
    original_title_convert_list = convert_half(original_title, operation_flags=0b001)
    for title in original_title_convert_list:
        if title != original_title:
            no_split_title_list.append(title)

    # 去重并保持顺序
    no_split_title_list = list(dict.fromkeys(no_split_title_list))
    
    # 如果标题长度不超过 min_length，则直接添加演员名
    if (
        len(original_title) <= min_length
        and best_match_actor
    ):
        print(f"标题长度未超过 min_length = {min_length}, 直接添加演员名")
        for idx in range(len(no_split_title_list)):
            no_split_title_list[idx] = no_split_title_list[idx] + " " + best_match_actor
        return no_split_title_list, no_split_title_list
        
    # 构造分隔符的正则表达式模式
    pattern_parts = []
    separator_list = [separator]
    if extra_separator:
        separator_list.extend(extra_separator.split(","))
    for each_sep in separator_list:
        pattern_parts.append(re.escape(each_sep))
    pattern = "|".join(pattern_parts)
    
    # 如果没有匹配到分隔符，直接返回基础标题列表
    if not re.search(pattern, original_title):
        print(f"标题无需分割, 直接返回基础标题列表")
        search_title_list = _add_actor_to_title(no_split_title_list, best_match_actor)
        return search_title_list, search_title_list
    
    def is_valid_part(part, actor_list):
        """
        判断一个片段是否有效
        1. 片段不能为空
        2. 片段不能为演员名
        3. 片段长度大于8或长度在4到8之间且不能为纯数字或字母或番号 例如: ABC-123
        """
        part = part.strip()
        if not part:
            return False
        if part in actor_list:
            return False
        if len(part) > 8:
            return True
        if len(part) >= 4 and not re.search(r"(^[a-zA-Z]+-\d+$)|(^[a-zA-Z0-9]+$)", part):
            return True
        print(f"标题片段 {part} 无效, 跳过")
        return False
    
    def split_and_filter(title, sep):
        """辅助函数：按分隔符拆分标题并过滤无效子串"""
        parts = title.split(sep)
        return [part for part in parts if is_valid_part(part, actor_list)]
    
    # 先以空格拆分
    split_title_with_space = []
    for title in no_split_title_list:
        split_title_with_space.extend(split_and_filter(title, separator))
    
    # 再以额外分隔符拆分
    if extra_separator:
        base_titles = split_title_with_space.copy() or no_split_title_list.copy()
        for extra in extra_separator.split(","):
            split_title_with_extra = []
            for title in base_titles:
                split_title_with_extra.extend(split_and_filter(title, extra))
            split_title_with_space.extend(split_title_with_extra)
    
    # 合并所有有效标题片段并去重
    titles_no_actor = list(dict.fromkeys(no_split_title_list + split_title_with_space))
    
    # 选取首位最符合的演员名添加到标题末尾
    no_split_title_list = _add_actor_to_title(no_split_title_list, best_match_actor)
    search_title_list = _add_actor_to_title(titles_no_actor, best_match_actor)
    return no_split_title_list, search_title_list


def _get_compare_title(title, actor_list, pattern=None, operation_flags=0b111):
    """
    正则删除标题中的pattern
    调用convert_half处理标题
    删除演员名
    返回删除演员名前后的标题, 如果标题本身没有演员名, 则返回的两个标题相同
    """
    if pattern:
        title = re.sub(pattern, "", title).strip()
    compare_title_list = convert_half(title, operation_flags)
    print(f"处理后的标题: {compare_title_list}")
    compare_title_no_actor = compare_title_list[0]
    for actor in actor_list:
        compare_title_no_actor = compare_title_no_actor.replace(actor, "").strip()
    print(f"处理后的标题(去除演员名): {compare_title_no_actor}")
    return compare_title_list[0], compare_title_no_actor


def _check_title_matching(compare_title,
                          amazon_compare_title,
                          no_split_compare_title,
                          length_diff_ratio=5,
                          min_match_length=5,
                          mid_title_length=12,
                          no_split_match_ratio=0.5,
                          split_match_ratio=0.8,
                          long_title_length=60,
                          length_ratio=0.5,
                          golden_ratio=0.618,
                          ):
    """
    功能:
        检测标题是否匹配, Amazon标题只需与未拆分标题匹配, 忽略拆分标题
    入参:
        no_split_compare_title 为处理过的未拆分标题
        amazon_compare_title 为处理过的Amazon标题
        二者均已经过以下操作:
        1. 正则去除【.*?】等内容
        2. 全角转半角
        3. 去除标点空格
        4. 去除敏感词
    匹配条件(按优先级排列):
        未拆分标题匹配, compare_title == no_split_compare_title
            a. len(长标题)/len(短标题) <= length_diff_ratio, 避免过短标题匹配到过长标题 (JUX-925)
            b. 匹配位置必须是Amazon的标题首字符 (ATID-586)
            c. 未拆分标题长度<=min_match_length, 要求短标题完全匹配长标题 (JUX-925)
            d. 未拆分标题长度>min_match_length 且 <=mid_title_length , 要求匹配长度>= min(min_match_length, len(短标题)) (ATID-586)
            e. 未拆分标题长度>mid_title_length, 要求匹配长度 >= min(math.floor(len(未拆分标题长度) * no_split_match_ratio), len(短标题))
        拆分标题匹配, compare_title != no_split_compare_title
            a. 先将Amazon标题与未拆分标题匹配, 匹配位置必须是Amazon的标题首字符
            b. 如果Amazon标题长度 <= min_match_length, 则要求Amazon标题完全匹配未拆分标题
            c. 如果Amazon标题长度 > min_match_length
                1). 要求匹配长度>= min_match_length, 这样是为了保证搜索的结果的前 min_match_length 个字符与未拆分标题相同
                2). 如果 len_no_split > long_title_length
                    要求 min(len_no_split, len_amazon)/max(len_no_split, len_amazon) >= length_ratio
                    要求匹配长度 >= math.floor(min(len_no_split, len_amazon) * golden_ratio)
                    这是专门针对超长标题且搜索结果雷同的系列影片 (HUNTA-145)
            d. 再将Amazon标题与拆分标题匹配匹配, 匹配位置必须是拆分标题首字符
            e. 拆分标题长度<= match.ceil(1.5 * min_match_length), 要求短标题完全匹配长标题
            f. 拆分标题长度> match.ceil(1.5 * min_match_length), 要求匹配长度 >= min(math.ceil(len(拆分标题长度) * split_match_ratio), len(短标题))
    返回:
        满足以上匹配条件返回 True, 否则返回 False
    """
    print(f"\n开始匹配标题")
    len_compare = len(compare_title)
    len_amazon = len(amazon_compare_title)
    len_no_split = len(no_split_compare_title)
    # 获取短标题和长标题
    short_title, long_title = (compare_title, amazon_compare_title) if len_compare < len_amazon else (amazon_compare_title, compare_title)
    len_short = len(short_title)
    len_long = len(long_title)
    # 匹配未拆分标题
    if compare_title == no_split_compare_title:
        print(f"标题未拆分, 遵循既定规则匹配\n未拆分标题标题: {compare_title}\nAmazon标题: {amazon_compare_title}")
        # 长字符串长度不能超过短字符串长度的5倍
        if len_long > length_diff_ratio * len_short:
            print(f"标题长度差异过大, 匹配失败!")
            return False

        if len_compare <= min_match_length:
            if short_title in long_title and amazon_compare_title.startswith(short_title):
                print(f"短标题完全匹配长标题, 匹配成功!")
                return True
            else:
                print(f"短标题不完全匹配长标题, 且未拆分标题长度 <={min_match_length}, 匹配失败!")
                return False
        elif len_no_split > min_match_length and len_no_split <= mid_title_length:
            required_match_length = min(min_match_length, len_short)  # 取短标题长度和 min_match_length 的最小值
            substring = amazon_compare_title[:required_match_length]  # 从 amazon_compare_title 的首字符开始截取
            if substring in compare_title:  # 判断子串是否出现在 compare_title 中
                print(f"匹配长度 >= {required_match_length}, 符合要求 , 匹配成功!")
                return True
            else:
                print(f"匹配长度 < {required_match_length}, 匹配失败!")
                return False
        else:
            required_match_length = min(math.floor(len_compare * no_split_match_ratio), len_short) # 取短标题长度和 len_compare * no_split_match_ratio 的最小值
            substring = amazon_compare_title[:required_match_length]  # 从 amazon_compare_title 的首字符开始截取
            if substring in compare_title:  # 判断子串是否出现在 compare_title 中
                print(f"匹配长度 >= {required_match_length}, 符合要求 , 匹配成功!")
                return True
            else:
                print(f"匹配长度 < {required_match_length}, 匹配失败!")
                return False
    else: # 匹配拆分标题
        print(f"标题已拆分, 遵循既定规则匹配\n拆分标题: {compare_title}\n未拆分标题: {no_split_compare_title}\nAmazon标题: {amazon_compare_title}")
        print(f"先与未拆分标题匹配")
        if len_amazon <= min_match_length:
            if amazon_compare_title in no_split_compare_title:
                print(f"Amazon标题完全匹配未拆分标题, 继续匹配")
                pass
            else:
                print(f"Amazon标题不完全匹配长标题, 且Amazon标题长度 <={min_match_length}, 匹配失败!")
                return False
        else:
            substring = amazon_compare_title[:min_match_length]  # 从 amazon_compare_title 的首字符开始截取
            if substring in no_split_compare_title:  # 判断子串是否出现在 no_split_compare_title 中
                print(f"Amazon标题与未拆分标题匹配长度 >= {min_match_length}, 继续匹配")
                pass
            else:
                print(f"Amazon标题与未拆分标题匹配长度 < {min_match_length}, 匹配失败!")
                return False
            if len_no_split > long_title_length:
                if min(len_no_split, len_amazon)/max(len_no_split, len_amazon) < length_ratio:
                    print(f"超长标题, 长度比 < {length_ratio}, 匹配失败!")
                    return False
                else:
                    print(f"超长标题, 长度比 >= {length_ratio}, 继续匹配")
                    pass
                required_match_length = math.floor(min(len_no_split, len_amazon) * golden_ratio)
                substring = amazon_compare_title[:required_match_length]  # 从 amazon_compare_title 的首字符开始截取
                if substring in no_split_compare_title:  # 判断子串是否出现在 no_split_compare_title 中
                    print(f"超长标题, 匹配率 >= {golden_ratio}, 符合要求, 继续匹配")
                    pass
                else:
                    print(f"超长标题, 匹配率 < {golden_ratio}, 匹配失败!")
                    return False
        print(f"未拆分标题匹配通过, 再与拆分标题匹配")
        if len_compare <= math.ceil(1.5 * min_match_length):
            if short_title in long_title and compare_title.startswith(short_title):
                print(f"短标题完全匹配长标题, 匹配成功!")
                return True
            else:
                print(f"拆分标题不完全匹配Amazon标题, 且拆分标题长度 <={math.ceil(1.5 * min_match_length)}, 匹配失败!")
                return False
        else:
            required_match_length = min(math.ceil(len_compare * split_match_ratio), len_short) # 取短标题长度和 len_compare * split_match_ratio 的最小值
            substring = compare_title[:required_match_length]  # 从 compare_title 的首字符开始截取
            if substring in amazon_compare_title:  # 判断子串是否出现在 amazon_compare_title 中
                print(f"匹配长度 >= {required_match_length}, 符合要求 , 匹配成功!")
                return True
            else:
                print(f"匹配长度 < {required_match_length}, 匹配失败!")
                return False

def _check_realse_date(json_data, amazon_title, promotion_keywords=[], amazon_release=None):
    """
    比较影片发行日期与Amazon详情页的发行日期是否一致, 避免同一个演员的相同标题影片被误匹配
    1. 如果有任何一个日期不存在, 则返回True
    2. 如果两个日期都存在, 则开始比较
    3. 如果二者间隔 <=30天返回True, 否则返回False, 这是因为有时候影片发行日期取的是配信日期, 会有一定的差异
    4. 如果二者间隔 >30天, 但影片标题中包含促销推广关键字, 则不认为日期不一致
    """
    print(f"开始检测发行日期是否匹配")
    movie_release = json_data.get("release")
    
    if not amazon_release:
        print(f"Amazon详情页无发行日期, 跳过此检测")
        return "NO RELEASE DATE"
    elif not movie_release:
        print(f"无影片发行日期, 跳过此检测")
        return "NO RELEASE DATE"
    else:
        # 影片发行日期, 格式为 2024-08-17
        movie_release_date = datetime.strptime(movie_release, "%Y-%m-%d")
        print(f"影片发行日期: {movie_release}")
        # Amazon详情页发行日期, 格式为 2024/8/17
        amazon_release_date = datetime.strptime(amazon_release, "%Y/%m/%d")
        print(f"Amazon详情页发行日期: {amazon_release_date.strftime('%Y-%m-%d')}")
    
    date_diff = abs((movie_release_date - amazon_release_date).days)
    if date_diff <= 30:
        return "SUCCESS"
    elif any(promotion in amazon_title for promotion in promotion_keywords):
        print(f"影片发行日期与Amazon详情页的发行日期有差异, 但影片标题中包含促销推广关键字, 跳过此检测")
        return "PROMOTION"
    return "ERROR"

def _check_detail_page(json_data, title_match_ele, actor_amazon):
    """
    获取amazon的详情页, 检测演员名是否匹配, 发行日期是否吻合
    返回:
        布尔值
    """
    detail_url = title_match_ele[1]
    amazon_title = title_match_ele[2]
    promotion_keywords = ["特選アウトレット", "ベストヒッツ"]
    try:
        url_new = "https://www.amazon.co.jp" + re.findall(r"(/dp/[^/]+)", detail_url)[0]
    except:
        
        url_new = detail_url
    print(f"\n详情页url: {url_new}")
    result, html_detail = get_amazon_data(url_new)
    if result and html_detail:
        html = etree.fromstring(html_detail, etree.HTMLParser())
        # 获取演员名
        detail_actor = html.xpath('//span[@class="author notFaded" and .//span[contains(text(), "(出演)")]]//a[@class="a-link-normal"]/text()')
        # 去除所有空格并过滤掉空字符串
        raw_detail_actor_list = []
        for each_actor in detail_actor:
            if each_actor.strip():
                each_actor.replace(" ", "")
                raw_detail_actor_list.extend(each_actor.split("/"))
        detail_actor_list = _split_actor(raw_detail_actor_list) if raw_detail_actor_list else []
        # detail_info_1 = str(
        #     html.xpath('//ul[@class="a-unordered-list a-vertical a-spacing-mini"]//text()')
        # ).replace(" ", "")
        # detail_info_2 = str(
        #     html.xpath('//div[@id="detailBulletsWrapper_feature_div"]//text()')
        # ).replace(" ", "")
        # detail_info_3 = str(html.xpath('//div[@id="productDescription"]//text()')).replace(" ", "")
        # all_info = detail_actor + detail_info_1 + detail_info_2 + detail_info_3
        # print(f"详情页信息: {all_info}")
        # 获取发行日期
        date_text = html.xpath("//span[contains(text(), '発売日')]/following-sibling::span[1]/text()")
        amazon_release = date_text[0].strip() if date_text else ""
        check_release = _check_realse_date(json_data, amazon_title, promotion_keywords, amazon_release)
        if check_release == "SUCCESS":
            print(f"发行日期匹配成功!")
            return "RELEASE MATCH"
        elif check_release in ["NO RELEASE DATE", "PROMOTION"]:
            if detail_actor_list:
                print(f"开始匹配演员\n详情页演员列表: {detail_actor_list}")
                for each_actor in actor_amazon:
                    if each_actor in detail_actor_list:
                        print(f"详情页匹配到演员: {each_actor}")
                        return "ACTOR MATCH"
                print(f"详情页演员不匹配, 跳过")
                return "ACTOR MISMATCH"
            else:
                print(f"详情页未找到演员, 跳过")
                return "LACK PROOF"
        else:
            print(f"发行日期不匹配, 跳过")
            return "RELEASE MISMATCH"
    print(f"详情页获取失败, 跳过")
    return "ERROR"

def get_big_pic_by_amazon(json_data, original_title, raw_actor_list):
    if not original_title or not raw_actor_list:
        return ""
    hd_pic_url = ""
    actor_list, best_match_actor = _get_actor_list(json_data, original_title, raw_actor_list)
    # 移除标题中匹配的pattern
    pattern = r"^\[.*?\]|【.*?】|DVD|（DOD）|（BOD）"
    # 拆分标题
    no_split_title_list, search_title_list = _split_title(original_title, actor_list, best_match_actor, min_length=3, pattern=pattern, separator=" ", extra_separator="！,…")
    # 将未拆分的标题进行处理
    no_split_compare_title, no_split_compare_title_no_actor  = _get_compare_title(no_split_title_list[-1], actor_list, pattern=pattern)
    # 图片url过滤集合, 如果匹配直接跳过
    pic_url_filtered_set = set()
    # 标题过滤集合, 如果匹配直接跳过
    amazon_title_filtered_set = set()
    # 标题匹配通过, 但详情页未找到有效匹配数据的图片url
    pic_legacy_list = []
    
    # 搜索标题
    for search_title in search_title_list:
        print(f"\n/********************开始搜索************************/")
        print(f"搜索标题总列表:\nsearch_title_list = {search_title_list}")
        print(f"搜索标题:\nsearch_title = {search_title}")
        print(f"图片url过滤集合:\npic_url_filtered_set = {pic_url_filtered_set}") 
        print(f"标题过滤集合:\namazon_title_filtered_set = {amazon_title_filtered_set}")
        if (
            search_title == search_title_list[0]
            and len(search_title) <= 3
        ):
            print(f"原始标题过短, 跳过: {search_title}")
            print(f"搜索包含演员名的原始标题")
            continue

        # 需要两次urlencode，nb_sb_noss表示无推荐来源
        url_search = (
            "https://www.amazon.co.jp/black-curtain/save-eligibility/black-curtain?returnUrl=/s?k="
            + urllib.parse.quote_plus(urllib.parse.quote_plus(search_title.replace("&", " ") + " [DVD]"))
            + "&ref=nb_sb_noss"
        )
        result, html_search = get_amazon_data(url_search)

        if result and html_search:
            # 页面显示 "没有找到与搜索匹配的商品。", 有时下方的推荐商品中会有正确的结果, 但不能保证百分百出现, 可能和cookie有关, 暂时未找到原因, 因此直接跳过
            if "検索に一致する商品はありませんでした。" in html_search:
                print(f"无搜索结果, 结束本次搜索\n")
                continue
            html = etree.fromstring(html_search, etree.HTMLParser())
            
            # 标题匹配列表
            title_match_list = []
            # 计算无效标题的数量, 避免在无效关键词上浪费过多时间
            invalid_title_count = 0
            amazon_result = html.xpath('//div[@class="a-section a-spacing-base"]')
            print(f"找到{len(amazon_result)}个结果")
            
            # 开始处理搜索结果
            for each_result in amazon_result:
                if invalid_title_count == 5:
                    print(f"无效标题数量过多, 结束本次搜索\n")
                    break
                amazon_category_list = each_result.xpath(
                    'div//a[@class="a-size-base a-link-normal s-underline-text s-underline-link-text s-link-style a-text-bold"]/text()'
                )
                amazon_title_list = each_result.xpath(
                    'div//h2[@class="a-size-base-plus a-spacing-none a-color-base a-text-normal"]/span/text()'
                )
                pic_url_list = each_result.xpath('div//div[@class="a-section aok-relative s-image-square-aspect"]/img/@src')
                detail_url_list = each_result.xpath('div//a[@class="a-link-normal s-no-outline"]/@href')
                
                if len(amazon_category_list) and len(pic_url_list) and (len(amazon_title_list) and len(detail_url_list)):
                    amazon_category = amazon_category_list[0]  # Amazon商品类型
                    amazon_title = amazon_title_list[0]  # Amazon商品标题
                    pic_url = pic_url_list[0]  # Amazon图片链接
                    pic_trunc_url = re.sub(r"\._[_]?AC_[^\.]+\.", ".", pic_url) # 去除后缀以获得更高分辨率的图片
                    detail_url = detail_url_list[0]  # Amazon详情页链接（有时带有演员名）
                    # 去除非 DVD与无图片的结果
                    if (amazon_category not in ["DVD", "Software Download"]
                        or ".jpg" not in pic_trunc_url
                    ):
                        print(f"\n无效标题, 跳过: {amazon_title}")
                        invalid_title_count += 1
                        print(f"添加到过滤集合")
                        amazon_title_filtered_set.add(amazon_title)
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    
                    if amazon_title in amazon_title_filtered_set:
                        invalid_title_count += 1
                        print(f"\n跳过已过滤的标题: {amazon_title}")
                        continue
                    
                    w, h = get_imgsize(pic_trunc_url)
                    if w < 700 or w >= h:
                        print(f"\n图片非高清或非竖版, 跳过: {pic_trunc_url}")
                        invalid_title_count += 1
                        print(f"添加到过滤集合")
                        amazon_title_filtered_set.add(amazon_title)
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    
                    # 避免单体作品取到合集结果 GVH-435
                    collection_keywords = ['BEST', '時間', '総集編', '完全', '枚組']
                    skip_flag = False
                    for collection_keyword in collection_keywords:
                        contains_s1 = collection_keyword in str(search_title_list[0]).upper()
                        contains_s2 = collection_keyword in str(amazon_title).upper()
                        if contains_s1 != contains_s2:
                            skip_flag = True
                    if skip_flag:
                        print(f"\n合集标题, 跳过: {amazon_title}")
                        invalid_title_count += 1
                        print(f"添加到过滤集合")
                        amazon_title_filtered_set.add(amazon_title)
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    print(f"\n+++++++++++++++++++++++++检测有效结果+++++++++++++++++++++++++")
                    print(f"搜索标题:\nsearch_title = {search_title}")
                    print(f"Amazon商品信息:\namazon_category = {amazon_category}\namazon_title = {amazon_title}\npic_trunc_url = {pic_trunc_url}")
                    if pic_trunc_url in pic_url_filtered_set:
                        print(f"\n跳过已过滤的图片url: {pic_trunc_url}")
                        continue
                    compare_title, compare_title_no_actor = _get_compare_title(search_title, actor_list, pattern=pattern)
                    amazon_compare_title, amazon_compare_title_no_actor = _get_compare_title(amazon_title, actor_list, pattern=pattern)
                    print(f"待比较的搜索标题:\ncompare_title = {compare_title}\ncompare_title_no_actor = {compare_title_no_actor}")
                    print(f"待比较的Amazon标题:\namazon_compare_title = {amazon_compare_title}\namazon_compare_title_no_actor = {amazon_compare_title_no_actor}")

                    # 判断标题是否匹配
                    print(f"待比较的未拆分标题:\nno_split_compare_title = {no_split_compare_title}\nno_split_compare_title_no_actor = {no_split_compare_title_no_actor}")
                    if (
                        _check_title_matching(compare_title, amazon_compare_title, no_split_compare_title)
                        or _check_title_matching(compare_title_no_actor, amazon_compare_title_no_actor, no_split_compare_title_no_actor)
                    ):
                        print(f"标题匹配成功")
                        detail_url = urllib.parse.unquote_plus(detail_url)
                        temp_title = re.findall(r"(.+)keywords=", detail_url)
                        temp_detail_url = (
                            temp_title[0] + amazon_compare_title if temp_title else detail_url + amazon_compare_title
                        )
                        detail_url_full = "https://www.amazon.co.jp" + detail_url

                        # 判断演员是否在标题里，避免同名标题误匹配 MOPP-023
                        for each_actor in actor_list:
                            if each_actor in temp_detail_url:
                                print(f"匹配演员: {each_actor}")
                                print(f"采用此结果")
                                hd_pic_url = pic_trunc_url
                                return hd_pic_url
                        else:
                            # 如果没有匹配任何演员，添加到 title_match_list
                            print(f"没有匹配到演员, 添加到 title_match_list")
                            title_match_list.append([pic_trunc_url, detail_url_full, amazon_title])
                            print(f"title_match_list_pic_only = {[element[0] for element in title_match_list]}")
                    else:
                        print(f"标题不匹配, 跳过: {amazon_title}")
                        invalid_title_count += 1
                        print(f"添加到过滤集合")
                        amazon_title_filtered_set.add(amazon_title)
                        pic_url_filtered_set.add(pic_trunc_url)
                else:
                    print(f"\n跳过不包含类型, 图片, 标题, 详情页面的结果")
                    invalid_title_count += 1
                    pass
                    
            # 当搜索结果匹配到标题，没有匹配到演员时，尝试去详情页获取演员信息
            if (len(title_match_list) > 0):
                print(f"\n尝试去详情页获取演员信息, 尝试最多4个结果")
                for each in title_match_list[:4]:
                    detail_page_match =  _check_detail_page(json_data, each, actor_list)
                    if detail_page_match in ["RELEASE MATCH", "ACTOR MATCH"]:
                        print(f"详情页检测通过, 采用此结果!")
                        return each[0]
                    elif detail_page_match == "LACK PROOF":
                        print(f"详情页未找到有效信息, 将图片url添加到保留列表")
                        pic_legacy_list.append(each[0])
                    else:
                        # 只添加图片url, 因为标题已经匹配, 否则添加演员名搜索时会过滤掉有效标题
                        print(f"详情页检测未通过, 将图片url添加到过滤集合")
                        pic_url_filtered_set.add(each[0])
                    
            # # 添加演员名重新搜索
            # actor_add_in_title = actor_list[0]
            # if (
            #     actor_add_in_title
            #     and actor_add_in_title not in search_title
            # ):
            #     title_with_actor = search_title + ' ' + actor_add_in_title
            #     if title_with_actor not in search_title_list:
            #         search_title_list.extend([title_with_actor])
            #         print(f"添加演员名 {actor_add_in_title} 至待搜索列表")

    if pic_legacy_list:
        pic_legacy_list = list(dict.fromkeys(pic_legacy_list))
        print(f"已经尝试所有可能搜索, 仍未找到确实匹配结果, 选取可能的结果")
        print(f"图片保留列表\npic_legacy_list = {pic_legacy_list}")
        return pic_legacy_list[0]
    return hd_pic_url


def trailer_download(json_data, folder_new_path, folder_old_path, naming_rule):
    start_time = time.time()
    download_files = config.download_files
    keep_files = config.keep_files
    trailer_name = config.trailer_name
    trailer_url = json_data["trailer"]
    trailer_old_folder_path = os.path.join(folder_old_path, "trailers")
    trailer_new_folder_path = os.path.join(folder_new_path, "trailers")

    # 预告片名字不含视频文件名（只让一个视频去下载即可）
    if trailer_name == 1:
        trailer_folder_path = os.path.join(folder_new_path, "trailers")
        trailer_file_name = "trailer.mp4"
        trailer_file_path = os.path.join(trailer_folder_path, trailer_file_name)

        # 预告片文件夹已在已处理列表时，返回（这时只需要下载一个，其他分集不需要下载）
        if trailer_folder_path in Flags.trailer_deal_set:
            return
        Flags.trailer_deal_set.add(trailer_folder_path)

        # 不下载不保留时删除返回
        if "trailer" not in download_files and "trailer" not in keep_files:
            # 删除目标文件夹即可，其他文件夹和文件已经删除了
            if os.path.exists(trailer_folder_path):
                shutil.rmtree(trailer_folder_path, ignore_errors=True)
            return

    else:
        # 预告片带文件名（每个视频都有机会下载，如果已有下载好的，则使用已下载的）
        trailer_file_name = naming_rule + "-trailer.mp4"
        trailer_folder_path = folder_new_path
        trailer_file_path = os.path.join(trailer_folder_path, trailer_file_name)

        # 不下载不保留时删除返回
        if "trailer" not in download_files and "trailer" not in keep_files:
            # 删除目标文件，删除预告片旧文件夹、新文件夹（deal old file时没删除）
            if os.path.exists(trailer_file_path):
                delete_file(trailer_file_path)
            if os.path.exists(trailer_old_folder_path):
                shutil.rmtree(trailer_old_folder_path, ignore_errors=True)
            if trailer_new_folder_path != trailer_old_folder_path and os.path.exists(trailer_new_folder_path):
                shutil.rmtree(trailer_new_folder_path, ignore_errors=True)
            return

    # 选择保留文件，当存在文件时，不下载。（done trailer path 未设置时，把当前文件设置为 done trailer path，以便其他分集复制）
    if "trailer" in keep_files and os.path.exists(trailer_file_path):
        if not Flags.file_done_dic.get(json_data["number"]).get("trailer"):
            Flags.file_done_dic[json_data["number"]].update({"trailer": trailer_file_path})
            # 带文件名时，删除掉新、旧文件夹，用不到了。（其他分集如果没有，可以复制第一个文件的预告片。此时不删，没机会删除了）
            if trailer_name == 0:
                if os.path.exists(trailer_old_folder_path):
                    shutil.rmtree(trailer_old_folder_path, ignore_errors=True)
                if trailer_new_folder_path != trailer_old_folder_path and os.path.exists(trailer_new_folder_path):
                    shutil.rmtree(trailer_new_folder_path, ignore_errors=True)
        json_data["logs"] += "\n 🍀 Trailer done! (old)(%ss) " % get_used_time(start_time)
        return True

    # 带文件名时，选择下载不保留，或者选择保留但没有预告片，检查是否有其他分集已下载或本地预告片
    # 选择下载不保留，当没有下载成功时，不会删除不保留的文件
    done_trailer_path = Flags.file_done_dic.get(json_data["number"]).get("trailer")
    if trailer_name == 0 and done_trailer_path and os.path.exists(done_trailer_path):
        if os.path.exists(trailer_file_path):
            delete_file(trailer_file_path)
        copy_file(done_trailer_path, trailer_file_path)
        json_data["logs"] += "\n 🍀 Trailer done! (copy trailer)(%ss)" % get_used_time(start_time)
        return

    # 不下载时返回（选择不下载保留，但本地并不存在，此时返回）
    if "trailer," not in download_files:
        return

    # 下载预告片,检测链接有效性
    content_length = check_url(trailer_url, length=True)
    if content_length:
        # 创建文件夹
        if trailer_name == 1 and not os.path.exists(trailer_folder_path):
            os.makedirs(trailer_folder_path)

        # 开始下载
        download_files = config.download_files
        signal.show_traceback_log(f"🍔 {json_data['number']} download trailer... {trailer_url}")
        trailer_file_path_temp = trailer_file_path
        if os.path.exists(trailer_file_path):
            trailer_file_path_temp = trailer_file_path + ".[DOWNLOAD].mp4"
        if download_file_with_filepath(json_data, trailer_url, trailer_file_path_temp, trailer_folder_path):
            file_size = os.path.getsize(trailer_file_path_temp)
            if file_size >= content_length or "ignore_size" in download_files:
                json_data["logs"] += "\n 🍀 Trailer done! ({} {}/{})({}s) ".format(
                    json_data["trailer_from"], file_size, content_length, get_used_time(start_time)
                )
                signal.show_traceback_log(f"✅ {json_data['number']} trailer done!")
                if trailer_file_path_temp != trailer_file_path:
                    move_file(trailer_file_path_temp, trailer_file_path)
                    delete_file(trailer_file_path_temp)
                done_trailer_path = Flags.file_done_dic.get(json_data["number"]).get("trailer")
                if not done_trailer_path:
                    Flags.file_done_dic[json_data["number"]].update({"trailer": trailer_file_path})
                    if trailer_name == 0:  # 带文件名，已下载成功，删除掉那些不用的文件夹即可
                        if os.path.exists(trailer_old_folder_path):
                            shutil.rmtree(trailer_old_folder_path, ignore_errors=True)
                        if trailer_new_folder_path != trailer_old_folder_path and os.path.exists(
                            trailer_new_folder_path
                        ):
                            shutil.rmtree(trailer_new_folder_path, ignore_errors=True)
                return True
            else:
                json_data["logs"] += "\n 🟠 Trailer size is incorrect! delete it! ({} {}/{}) ".format(
                    json_data["trailer_from"], file_size, content_length
                )
        # 删除下载失败的文件
        delete_file(trailer_file_path_temp)
        json_data["logs"] += "\n 🟠 Trailer download failed! (%s) " % trailer_url

    if os.path.exists(trailer_file_path):  # 使用旧文件
        done_trailer_path = Flags.file_done_dic.get(json_data["number"]).get("trailer")
        if not done_trailer_path:
            Flags.file_done_dic[json_data["number"]].update({"trailer": trailer_file_path})
            if trailer_name == 0:  # 带文件名，已下载成功，删除掉那些不用的文件夹即可
                if os.path.exists(trailer_old_folder_path):
                    shutil.rmtree(trailer_old_folder_path, ignore_errors=True)
                if trailer_new_folder_path != trailer_old_folder_path and os.path.exists(trailer_new_folder_path):
                    shutil.rmtree(trailer_new_folder_path, ignore_errors=True)
        json_data["logs"] += "\n 🟠 Trailer download failed! 将继续使用之前的本地文件！"
        json_data["logs"] += "\n 🍀 Trailer done! (old)(%ss)" % get_used_time(start_time)
        return True


def _get_big_thumb(json_data):
    """
    获取背景大图：
    1，官网图片
    2，Amazon 图片
    3，Google 搜图
    """
    start_time = time.time()
    if "thumb" not in config.download_hd_pics:
        return json_data
    number = json_data["number"]
    letters = json_data["letters"]
    number_lower_line = number.lower()
    number_lower_no_line = number_lower_line.replace("-", "")
    thumb_width = 0

    if json_data["cover_from"] == 'dmm':
        if json_data["cover"]:
            thumb_width, h = get_imgsize(json_data["cover"])
            print(f"dmm thumb_width = {thumb_width}")
            # 对于存在 dmm 2K 横版封面的影片, 直接下载其竖版封面
            if thumb_width >= 1700:
                json_data["logs"] += "\n 🖼 HD Thumb found! ({})({}s)".format(
                    json_data["cover_from"], get_used_time(start_time)
                )
                json_data["poster_big"] = True
                return json_data
    # faleno.jp 番号检查，都是大图，返回即可
    elif json_data["cover_from"] in ["faleno", "dahlia"]:
        if json_data["cover"]:
            json_data["logs"] += "\n 🖼 HD Thumb found! ({})({}s)".format(
                json_data["cover_from"], get_used_time(start_time)
            )
        json_data["poster_big"] = True
        return json_data

    # prestige 图片有的是大图，需要检测图片分辨率
    elif json_data["cover_from"] in ["prestige", "mgstage"]:
        if json_data["cover"]:
            thumb_width, h = get_imgsize(json_data["cover"])

    # 片商官网查询
    elif "official" in config.download_hd_pics:
        # faleno.jp 番号检查
        if re.findall(r"F[A-Z]{2}SS", number):
            req_url = "https://faleno.jp/top/works/%s/" % number_lower_no_line
            result, response = get_html(req_url)
            if result:
                temp_url = re.findall(
                    r'src="((https://cdn.faleno.net/top/wp-content/uploads/[^_]+_)([^?]+))\?output-quality=', response
                )
                if temp_url:
                    json_data["cover"] = temp_url[0][0]
                    json_data["poster"] = temp_url[0][1] + "2125.jpg"
                    json_data["cover_from"] = "faleno"
                    json_data["poster_from"] = "faleno"
                    json_data["poster_big"] = True
                    trailer_temp = re.findall(r'class="btn09"><a class="pop_sample" href="([^"]+)', response)
                    if trailer_temp:
                        json_data["trailer"] = trailer_temp[0]
                        json_data["trailer_from"] = "faleno"
                    json_data["logs"] += "\n 🖼 HD Thumb found! (faleno)(%ss)" % get_used_time(start_time)
                    return json_data

        # km-produce.com 番号检查
        number_letter = letters.lower()
        kmp_key = ["vrkm", "mdtm", "mkmp", "savr", "bibivr", "scvr", "slvr", "averv", "kbvr", "cbikmv"]
        prestige_key = ["abp", "abw", "aka", "prdvr", "pvrbst", "sdvr", "docvr"]
        if number_letter in kmp_key:
            req_url = f"https://km-produce.com/img/title1/{number_lower_line}.jpg"
            real_url = check_url(req_url)
            if real_url:
                json_data["cover"] = real_url
                json_data["cover_from"] = "km-produce"
                json_data["logs"] += "\n 🖼 HD Thumb found! (km-produce)(%ss)" % (get_used_time(start_time))
                return json_data

        # www.prestige-av.com 番号检查
        elif number_letter in prestige_key:
            number_num = re.findall(r"\d+", number)[0]
            if number_letter == "abw" and int(number_num) > 280:
                pass
            else:
                req_url = f"https://www.prestige-av.com/api/media/goods/prestige/{number_letter}/{number_num}/pb_{number_lower_line}.jpg"
                if number_letter == "docvr":
                    req_url = f"https://www.prestige-av.com/api/media/goods/doc/{number_letter}/{number_num}/pb_{number_lower_line}.jpg"
                if get_imgsize(req_url)[0] >= 800:
                    json_data["cover"] = req_url
                    json_data["poster"] = req_url.replace("/pb_", "/pf_")
                    json_data["cover_from"] = "prestige"
                    json_data["poster_from"] = "prestige"
                    json_data["poster_big"] = True
                    json_data["logs"] += "\n 🖼 HD Thumb found! (prestige)(%ss)" % (get_used_time(start_time))
                    return json_data

    # 使用google以图搜图
    pic_url = json_data.get("cover")
    if "google" in config.download_hd_pics:
        if pic_url and json_data["cover_from"] != "theporndb":
            thumb_url, cover_size = get_big_pic_by_google(pic_url)
            if thumb_url and cover_size[0] > thumb_width:
                json_data["cover_size"] = cover_size
                pic_domain = re.findall(r"://([^/]+)", thumb_url)[0]
                json_data["cover_from"] = f"Google({pic_domain})"
                json_data["cover"] = thumb_url
                json_data["logs"] += "\n 🖼 HD Thumb found! ({})({}s)".format(
                    json_data["cover_from"], get_used_time(start_time)
                )

    return json_data


def _get_big_poster(json_data):
    start_time = time.time()

    # 未勾选下载高清图poster时，返回
    if "poster" not in config.download_hd_pics:
        return json_data

    # 如果有大图时，直接下载
    if json_data.get("poster_big") and get_imgsize(json_data["poster"])[1] > 600:
        json_data["image_download"] = True
        json_data["logs"] += f"\n 🖼 HD Poster found! ({json_data['poster_from']})({get_used_time(start_time)}s)"
        return json_data

    # 初始化数据
    number = json_data.get("number")
    poster_url = json_data.get("poster")
    hd_pic_url = ""
    poster_width = 0

    # 通过原标题去 amazon 查询
    if "amazon" in config.download_hd_pics and json_data["mosaic"] in [
        "有码",
        "有碼",
        "流出",
        "无码流出",
        "无码破解",
        "無碼破解",
        "里番",
        "裏番",
        "动漫",
        "動漫",
    ]:
        hd_pic_url = get_big_pic_by_amazon(json_data, json_data["originaltitle_amazon"], json_data["actor_amazon"])
        if hd_pic_url:
            json_data["poster"] = hd_pic_url
            json_data["poster_from"] = "Amazon"
        if json_data["poster_from"] == "Amazon":
            json_data["image_download"] = True

    # 通过番号去 官网 查询获取稍微大一些的封面图，以便去 Google 搜索
    if (
        not hd_pic_url
        and "official" in config.download_hd_pics
        and "official" not in config.website_set
        and json_data["poster_from"] != "Amazon"
    ):
        letters = json_data["letters"].upper()
        official_url = config.official_websites.get(letters)
        if official_url:
            url_search = official_url + "/search/list?keyword=" + number.replace("-", "")
            result, html_search = get_html(url_search)
            if result:
                poster_url_list = re.findall(r'img class="c-main-bg lazyload" data-src="([^"]+)"', html_search)
                if poster_url_list:
                    # 使用官网图作为封面去 google 搜索
                    poster_url = poster_url_list[0]
                    json_data["poster"] = poster_url
                    json_data["poster_from"] = official_url.split(".")[-2].replace("https://", "")
                    # vr作品或者官网图片高度大于500时，下载封面图开
                    if "VR" in number.upper() or get_imgsize(poster_url)[1] > 500:
                        json_data["image_download"] = True

    # 使用google以图搜图，放在最后是因为有时有错误，比如 kawd-943
    poster_url = json_data.get("poster")
    if (
        not hd_pic_url
        and poster_url
        and "google" in config.download_hd_pics
        and json_data["poster_from"] != "theporndb"
    ):
        hd_pic_url, poster_size = get_big_pic_by_google(poster_url, poster=True)
        if hd_pic_url:
            if "prestige" in json_data["poster"] or json_data["poster_from"] == "Amazon":
                poster_width = get_imgsize(poster_url)[0]
            if poster_size[0] > poster_width:
                json_data["poster"] = hd_pic_url
                json_data["poster_size"] = poster_size
                pic_domain = re.findall(r"://([^/]+)", hd_pic_url)[0]
                json_data["poster_from"] = f"Google({pic_domain})"

    # 如果找到了高清链接，则替换
    if hd_pic_url:
        json_data["image_download"] = True
        json_data["logs"] += "\n 🖼 HD Poster found! ({})({}s)".format(
            json_data["poster_from"], get_used_time(start_time)
        )

    return json_data


def thumb_download(json_data, folder_new_path, thumb_final_path):
    start_time = time.time()
    poster_path = json_data["poster_path"]
    thumb_path = json_data["thumb_path"]
    fanart_path = json_data["fanart_path"]

    # 本地存在 thumb.jpg，且勾选保留旧文件时，不下载
    if thumb_path and "thumb" in config.keep_files:
        json_data["logs"] += "\n 🍀 Thumb done! (old)(%ss) " % get_used_time(start_time)
        return True

    # 如果thumb不下载，看fanart、poster要不要下载，都不下载则返回
    if "thumb" not in config.download_files:
        if "poster" in config.download_files and ("poster" not in config.keep_files or not poster_path):
            pass
        elif "fanart" in config.download_files and ("fanart" not in config.keep_files or not fanart_path):
            pass
        else:
            return True

    # 尝试复制其他分集。看分集有没有下载，如果下载完成则可以复制，否则就自行下载
    if json_data["cd_part"]:
        done_thumb_path = Flags.file_done_dic.get(json_data["number"]).get("thumb")
        if (
            done_thumb_path
            and os.path.exists(done_thumb_path)
            and split_path(done_thumb_path)[0] == split_path(thumb_final_path)[0]
        ):
            copy_file(done_thumb_path, thumb_final_path)
            json_data["logs"] += "\n 🍀 Thumb done! (copy cd-thumb)(%ss) " % get_used_time(start_time)
            json_data["cover_from"] = "copy cd-thumb"
            json_data["thumb_path"] = thumb_final_path
            return True

    # 获取高清背景图
    json_data = _get_big_thumb(json_data)

    # 下载图片
    cover_url = json_data.get("cover")
    cover_from = json_data.get("cover_from")
    if cover_url:
        cover_list = json_data["cover_list"]
        while [cover_from, cover_url] in cover_list:
            cover_list.remove([cover_from, cover_url])
        cover_list.insert(0, [cover_from, cover_url])

        thumb_final_path_temp = thumb_final_path
        if os.path.exists(thumb_final_path):
            thumb_final_path_temp = thumb_final_path + ".[DOWNLOAD].jpg"
        for each in cover_list:
            if not each[1]:
                continue
            cover_from, cover_url = each
            cover_url = check_url(cover_url)
            if not cover_url:
                json_data["logs"] += (
                    f"\n 🟠 检测到 Thumb 图片失效! 跳过！({cover_from})({get_used_time(start_time)}s) " + each[1]
                )
                continue
            json_data["cover_from"] = cover_from
            if download_file_with_filepath(json_data, cover_url, thumb_final_path_temp, folder_new_path):
                cover_size = check_pic(thumb_final_path_temp)
                if cover_size:
                    if (
                        not cover_from.startswith("Google")
                        or cover_size == json_data["cover_size"]
                        or (
                            cover_size[0] >= 800
                            and abs(
                                cover_size[0] / cover_size[1] - json_data["cover_size"][0] / json_data["cover_size"][1]
                            )
                            <= 0.1
                        )
                    ):
                        # 图片下载正常，替换旧的 thumb.jpg
                        if thumb_final_path_temp != thumb_final_path:
                            move_file(thumb_final_path_temp, thumb_final_path)
                            delete_file(thumb_final_path_temp)
                        if json_data["cd_part"]:
                            dic = {"thumb": thumb_final_path}
                            Flags.file_done_dic[json_data["number"]].update(dic)
                        json_data["thumb_marked"] = False  # 表示还没有走加水印流程
                        json_data["logs"] += "\n 🍀 Thumb done! ({})({}s) ".format(
                            json_data["cover_from"], get_used_time(start_time)
                        )
                        json_data["thumb_path"] = thumb_final_path
                        return True
                    else:
                        delete_file(thumb_final_path_temp)
                        json_data["logs"] += (
                            f"\n 🟠 检测到 Thumb 分辨率不对{str(cover_size)}! 已删除 ({cover_from})({get_used_time(start_time)}s)"
                        )
                        continue
                json_data["logs"] += f"\n 🟠 Thumb download failed! {cover_from}: {cover_url} "
    else:
        json_data["logs"] += "\n 🟠 Thumb url is empty! "

    # 下载失败，本地有图
    if thumb_path:
        json_data["logs"] += "\n 🟠 Thumb download failed! 将继续使用之前的图片！"
        json_data["logs"] += "\n 🍀 Thumb done! (old)(%ss) " % get_used_time(start_time)
        return True
    else:
        if "ignore_pic_fail" in config.download_files:
            json_data["logs"] += "\n 🟠 Thumb download failed! (你已勾选「图片下载失败时，不视为失败！」) "
            json_data["logs"] += "\n 🍀 Thumb done! (none)(%ss)" % get_used_time(start_time)
            return True
        else:
            json_data["logs"] += (
                "\n 🔴 Thumb download failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」 "
            )
            json_data["error_info"] = (
                "Thumb download failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」"
            )
            return False


def poster_download(json_data, folder_new_path, poster_final_path):
    start_time = time.time()
    download_files = config.download_files
    keep_files = config.keep_files
    poster_path = json_data["poster_path"]
    thumb_path = json_data["thumb_path"]
    fanart_path = json_data["fanart_path"]
    image_cut = ""

    # 不下载poster、不保留poster时，返回
    if "poster" not in download_files and "poster" not in keep_files:
        if poster_path:
            delete_file(poster_path)
        return True

    # 本地有poster时，且勾选保留旧文件时，不下载
    if poster_path and "poster" in keep_files:
        json_data["logs"] += "\n 🍀 Poster done! (old)(%ss)" % get_used_time(start_time)
        return True

    # 不下载时返回
    if "poster" not in download_files:
        return True

    # 尝试复制其他分集。看分集有没有下载，如果下载完成则可以复制，否则就自行下载
    if json_data["cd_part"]:
        done_poster_path = Flags.file_done_dic.get(json_data["number"]).get("poster")
        if (
            done_poster_path
            and os.path.exists(done_poster_path)
            and split_path(done_poster_path)[0] == split_path(poster_final_path)[0]
        ):
            copy_file(done_poster_path, poster_final_path)
            json_data["poster_from"] = "copy cd-poster"
            json_data["poster_path"] = poster_final_path
            json_data["logs"] += "\n 🍀 Poster done! (copy cd-poster)(%ss)" % get_used_time(start_time)
            return True

    # 勾选复制 thumb时：国产，复制thumb;无码，勾选不裁剪时，也复制thumb
    if thumb_path:
        mosaic = json_data["mosaic"]
        number = json_data["number"]
        copy_flag = False
        if number.startswith("FC2"):
            image_cut = "center"
            if "ignore_fc2" in download_files:
                copy_flag = True
        elif mosaic == "国产" or mosaic == "國產":
            image_cut = "right"
            if "ignore_guochan" in download_files:
                copy_flag = True
        elif mosaic == "无码" or mosaic == "無碼" or mosaic == "無修正":
            image_cut = "center"
            if "ignore_wuma" in download_files:
                copy_flag = True
        elif mosaic == "有码" or mosaic == "有碼":
            if "ignore_youma" in download_files:
                copy_flag = True
        if copy_flag:
            copy_file(thumb_path, poster_final_path)
            json_data["poster_marked"] = json_data["thumb_marked"]
            json_data["poster_from"] = "copy thumb"
            json_data["poster_path"] = poster_final_path
            json_data["logs"] += "\n 🍀 Poster done! (copy thumb)(%ss)" % get_used_time(start_time)
            return True

    # 获取高清 poster
    json_data = _get_big_poster(json_data)

    # 下载图片
    poster_url = json_data.get("poster")
    poster_from = json_data.get("poster_from")
    poster_final_path_temp = poster_final_path
    if os.path.exists(poster_final_path):
        poster_final_path_temp = poster_final_path + ".[DOWNLOAD].jpg"
    if json_data["image_download"]:
        start_time = time.time()
        if download_file_with_filepath(json_data, poster_url, poster_final_path_temp, folder_new_path):
            poster_size = check_pic(poster_final_path_temp)
            if poster_size:
                if (
                    not poster_from.startswith("Google")
                    or poster_size == json_data["poster_size"]
                    or "media-amazon.com" in poster_url
                ):
                    if poster_final_path_temp != poster_final_path:
                        move_file(poster_final_path_temp, poster_final_path)
                        delete_file(poster_final_path_temp)
                    if json_data["cd_part"]:
                        dic = {"poster": poster_final_path}
                        Flags.file_done_dic[json_data["number"]].update(dic)
                    json_data["poster_marked"] = False  # 下载的图，还没加水印
                    json_data["poster_path"] = poster_final_path
                    json_data["logs"] += f"\n 🍀 Poster done! ({poster_from})({get_used_time(start_time)}s)"
                    return True
                else:
                    delete_file(poster_final_path_temp)
                    json_data["logs"] += f"\n 🟠 检测到 Poster 分辨率不对{str(poster_size)}! 已删除 ({poster_from})"

    # 判断之前有没有 poster 和 thumb
    if not poster_path and not thumb_path:
        json_data["poster_path"] = ""
        if "ignore_pic_fail" in download_files:
            json_data["logs"] += "\n 🟠 Poster download failed! (你已勾选「图片下载失败时，不视为失败！」) "
            json_data["logs"] += "\n 🍀 Poster done! (none)(%ss)" % get_used_time(start_time)
            return True
        else:
            json_data["logs"] += (
                "\n 🔴 Poster download failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」 "
            )
            json_data["error_info"] = (
                "Poster download failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」"
            )
            return False

    # 使用thumb裁剪
    poster_final_path_temp = poster_final_path + ".[CUT].jpg"
    if fanart_path:
        thumb_path = fanart_path
    if cut_thumb_to_poster(json_data, thumb_path, poster_final_path_temp, image_cut):
        # 裁剪成功，替换旧图
        move_file(poster_final_path_temp, poster_final_path)
        if json_data["cd_part"]:
            dic = {"poster": poster_final_path}
            Flags.file_done_dic[json_data["number"]].update(dic)
        json_data["poster_path"] = poster_final_path
        json_data["poster_marked"] = False
        return True

    # 裁剪失败，本地有图
    if poster_path:
        json_data["logs"] += "\n 🟠 Poster cut failed! 将继续使用之前的图片！"
        json_data["logs"] += "\n 🍀 Poster done! (old)(%ss) " % get_used_time(start_time)
        return True
    else:
        if "ignore_pic_fail" in download_files:
            json_data["logs"] += "\n 🟠 Poster cut failed! (你已勾选「图片下载失败时，不视为失败！」) "
            json_data["logs"] += "\n 🍀 Poster done! (none)(%ss)" % get_used_time(start_time)
            return True
        else:
            json_data["logs"] += (
                "\n 🔴 Poster cut failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」 "
            )
            json_data["error_info"] = "Poster failed！你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」"
            return False


def fanart_download(json_data, fanart_final_path):
    """
    复制thumb为fanart
    """
    start_time = time.time()
    thumb_path = json_data["thumb_path"]
    fanart_path = json_data["fanart_path"]
    download_files = config.download_files
    keep_files = config.keep_files

    # 不保留不下载时删除返回
    if ",fanart" not in keep_files and ",fanart" not in download_files:
        if fanart_path and os.path.exists(fanart_path):
            delete_file(fanart_path)
        return True

    # 保留，并且本地存在 fanart.jpg，不下载返回
    if ",fanart" in keep_files and fanart_path:
        json_data["logs"] += "\n 🍀 Fanart done! (old)(%ss)" % get_used_time(start_time)
        return True

    # 不下载时，返回
    if ",fanart" not in download_files:
        return True

    # 尝试复制其他分集。看分集有没有下载，如果下载完成则可以复制，否则就自行下载
    if json_data["cd_part"]:
        done_fanart_path = Flags.file_done_dic.get(json_data["number"]).get("fanart")
        if (
            done_fanart_path
            and os.path.exists(done_fanart_path)
            and split_path(done_fanart_path)[0] == split_path(fanart_final_path)[0]
        ):
            if fanart_path:
                delete_file(fanart_path)
            copy_file(done_fanart_path, fanart_final_path)
            json_data["fanart_from"] = "copy cd-fanart"
            json_data["fanart_path"] = fanart_final_path
            json_data["logs"] += "\n 🍀 Fanart done! (copy cd-fanart)(%ss)" % get_used_time(start_time)
            return True

    # 复制thumb
    if thumb_path:
        if fanart_path:
            delete_file(fanart_path)
        copy_file(thumb_path, fanart_final_path)
        json_data["fanart_from"] = "copy thumb"
        json_data["fanart_path"] = fanart_final_path
        json_data["fanart_marked"] = json_data["thumb_marked"]
        json_data["logs"] += "\n 🍀 Fanart done! (copy thumb)(%ss)" % get_used_time(start_time)
        if json_data["cd_part"]:
            dic = {"fanart": fanart_final_path}
            Flags.file_done_dic[json_data["number"]].update(dic)
        return True
    else:
        # 本地有 fanart 时，不下载
        if fanart_path:
            json_data["logs"] += "\n 🟠 Fanart copy failed! 未找到 thumb 图片，将继续使用之前的图片！"
            json_data["logs"] += "\n 🍀 Fanart done! (old)(%ss)" % get_used_time(start_time)
            return True

        else:
            if "ignore_pic_fail" in download_files:
                json_data["logs"] += "\n 🟠 Fanart failed! (你已勾选「图片下载失败时，不视为失败！」) "
                json_data["logs"] += "\n 🍀 Fanart done! (none)(%ss)" % get_used_time(start_time)
                return True
            else:
                json_data["logs"] += (
                    "\n 🔴 Fanart failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」 "
                )
                json_data["error_info"] = (
                    "Fanart 下载失败！你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」"
                )
                return False


def extrafanart_download(json_data, folder_new_path):
    start_time = time.time()
    download_files = config.download_files
    keep_files = config.keep_files
    extrafanart_list = json_data.get("extrafanart")
    extrafanart_folder_path = os.path.join(folder_new_path, "extrafanart")

    # 不下载不保留时删除返回
    if "extrafanart" not in download_files and "extrafanart" not in keep_files:
        if os.path.exists(extrafanart_folder_path):
            shutil.rmtree(extrafanart_folder_path, ignore_errors=True)
        return

    # 本地存在 extrafanart_folder，且勾选保留旧文件时，不下载
    if "extrafanart" in keep_files and os.path.exists(extrafanart_folder_path):
        json_data["logs"] += "\n 🍀 Extrafanart done! (old)(%ss) " % get_used_time(start_time)
        return True

    # 如果 extrafanart 不下载
    if "extrafanart" not in download_files:
        return True

    # 检测链接有效性
    if extrafanart_list and check_url(extrafanart_list[0]):
        extrafanart_folder_path_temp = extrafanart_folder_path
        if os.path.exists(extrafanart_folder_path_temp):
            extrafanart_folder_path_temp = extrafanart_folder_path + "[DOWNLOAD]"
            if not os.path.exists(extrafanart_folder_path_temp):
                os.makedirs(extrafanart_folder_path_temp)
        else:
            os.makedirs(extrafanart_folder_path_temp)

        extrafanart_count = 0
        extrafanart_count_succ = 0
        task_list = []
        for extrafanart_url in extrafanart_list:
            extrafanart_count += 1
            extrafanart_name = "fanart" + str(extrafanart_count) + ".jpg"
            extrafanart_file_path = os.path.join(extrafanart_folder_path_temp, extrafanart_name)
            task_list.append(
                [json_data, extrafanart_url, extrafanart_file_path, extrafanart_folder_path_temp, extrafanart_name]
            )
        extrafanart_pool = Pool(20)  # 剧照下载线程池
        result = extrafanart_pool.map(_mutil_extrafanart_download_thread, task_list)
        for res in result:
            if res:
                extrafanart_count_succ += 1
        if extrafanart_count_succ == extrafanart_count:
            if extrafanart_folder_path_temp != extrafanart_folder_path:
                shutil.rmtree(extrafanart_folder_path)
                os.rename(extrafanart_folder_path_temp, extrafanart_folder_path)
            json_data["logs"] += "\n 🍀 ExtraFanart done! ({} {}/{})({}s)".format(
                json_data["extrafanart_from"], extrafanart_count_succ, extrafanart_count, get_used_time(start_time)
            )
            return True
        else:
            json_data["logs"] += "\n 🟠  ExtraFanart download failed! ({} {}/{})({}s)".format(
                json_data["extrafanart_from"], extrafanart_count_succ, extrafanart_count, get_used_time(start_time)
            )
            if extrafanart_folder_path_temp != extrafanart_folder_path:
                shutil.rmtree(extrafanart_folder_path_temp)
            else:
                json_data["logs"] += "\n 🍀 ExtraFanart done! (incomplete)(%ss)" % get_used_time(start_time)
                return False
        json_data["logs"] += "\n 🟠 ExtraFanart download failed! 将继续使用之前的本地文件！"
    if os.path.exists(extrafanart_folder_path):  # 使用旧文件
        json_data["logs"] += "\n 🍀 ExtraFanart done! (old)(%ss)" % get_used_time(start_time)
        return True


def show_netstatus():
    signal.show_net_info(time.strftime("%Y-%m-%d %H:%M:%S").center(80, "="))
    proxy_type = ""
    retry_count = 0
    proxy = ""
    timeout = 0
    try:
        proxy_type, proxy, timeout, retry_count = config.type, config.proxy, config.timeout, config.retry
    except:
        signal.show_traceback_log(traceback.format_exc())
        signal.show_net_info(traceback.format_exc())
    if proxy == "" or proxy_type == "" or proxy_type == "no":
        signal.show_net_info(
            " 当前网络状态：❌ 未启用代理\n   类型： "
            + str(proxy_type)
            + "    地址："
            + str(proxy)
            + "    超时时间："
            + str(timeout)
            + "    重试次数："
            + str(retry_count)
        )
    else:
        signal.show_net_info(
            " 当前网络状态：✅ 已启用代理\n   类型： "
            + proxy_type
            + "    地址："
            + proxy
            + "    超时时间："
            + str(timeout)
            + "    重试次数："
            + str(retry_count)
        )
    signal.show_net_info("=" * 80)


def check_proxyChange():
    new_proxy = (config.type, config.proxy, config.timeout, config.retry)
    if Flags.current_proxy:
        if new_proxy != Flags.current_proxy:
            signal.show_net_info("\n🌈 代理设置已改变：")
            show_netstatus()
    Flags.current_proxy = new_proxy

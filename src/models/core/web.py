"""
刮削过程的网络操作
"""

import os
import re
import shutil
import time
import traceback
import urllib

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


def get_actor_list(json_data, title, raw_actor_list):
    """
    对含有 （）的演员名进行拆分整合, 返回去重列表和标题中的演员名 
    """
    print(f"原始标题: {title}\n原始演员列表: {raw_actor_list}")
    def split_actor(raw_actor_list):
        # 创建一个空集合用于存储结果
        actor_set = set()
        # 遍历列表
        for item in raw_actor_list:
            # 使用正则表达式匹配括号内容
            match = re.match(r"(.+?)[（\(](.+?)[）\)]", item)
            if match:  # 如果匹配成功
                name_before_bracket = match.group(1).strip()  # 括号前的内容
                name_in_bracket = match.group(2).strip()      # 括号内的内容
                
                # 将拆分后的两个部分添加到集合中
                actor_set.add(name_before_bracket)
                actor_set.add(name_in_bracket)
            else:
                # 如果没有括号，直接添加到集合中
                actor_set.add(item.strip())

        # 将集合转换为列表
        return list(actor_set)
    
    actor_in_title_list = []
    actor_list = split_actor(raw_actor_list)
    for item in actor_list:
        if str(item).upper() in str(title).upper():
            actor_in_title_list.append(item)
            break
    if not actor_in_title_list:
        amazon_orginaltitle_actor = json_data.get("amazon_orginaltitle_actor")
        actor_in_title_list = split_actor = ([amazon_orginaltitle_actor])
    print(f"整合后的演员列表: {actor_list}\n标题中的演员列表: {actor_in_title_list}")
    return actor_list, actor_in_title_list

def get_halfwidth_no_actor_title(title, actor_list, operation_flags=0b111):
    halfwidth_title = convert_half(title, operation_flags)
    for actor in actor_list:
        halfwidth_title = halfwidth_title.replace(actor, "")
    halfwidth_no_actor_title = halfwidth_title.strip()
    return halfwidth_title, halfwidth_no_actor_title

def split_title(original_title, actor_list, separator=" ", extra_separator=None):
    """
    1. 移除标题中【】及其内容；
    2. 对标题进行敏感词转换，若转换后结果不同则加入结果列表；
    3. 构建分隔符正则表达式；
    4. 若原始标题不包含任何分隔符，则直接返回基础标题列表；
    5. 否则按主分隔符拆分并过滤无效子串；
    6. 若有额外分隔符，继续按其拆分；
    7. 最终返回原始标题与敏感词转换后的标题列表，以及所有有效标题片段的去重列表。
    """
    
    # 去除【】内的内容
    original_title = re.sub(r"【.*?】", "", original_title).strip()
    original_title = original_title.replace("（DOD）", "").strip()
    
    # 初始化基础标题列表
    original_title_base_list = [original_title]
    
    # 敏感词替换 APAK-162
    original_title_special = convert_half(original_title, operation_flags=0b001)
    if original_title_special != original_title:
        original_title_base_list.append(original_title_special)
    
    # 去重并保持顺序
    original_title_base_list = list(dict.fromkeys(original_title_base_list))
    
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
        return original_title_base_list, original_title_base_list
    
    def is_valid_part(part, actor_list):
        """判断一个片段是否有效"""
        part = part.strip()
        if not part:
            return False
        if part in actor_list:
            return False
        if len(part) > 8:
            return True
        if len(part) > 4 and not re.search(r"(^[a-zA-Z]+-\d+$)|(^[a-zA-Z0-9]+$)", part):
            return True
        return False
    
    def split_and_filter(title, sep):
        """辅助函数：按分隔符拆分标题并过滤无效子串"""
        parts = title.split(sep)
        return [part for part in parts if is_valid_part(part, actor_list)]
    
    # 先以空格拆分
    split_title_with_space = []
    for title in original_title_base_list:
        split_title_with_space.extend(split_and_filter(title, separator))
    
    # 再以额外分隔符拆分
    if extra_separator:
        for extra in extra_separator.split(","):
            split_title_with_extra = []
            base_titles = split_title_with_space or original_title_base_list
            for title in base_titles:
                split_title_with_extra.extend(split_and_filter(title, extra))
            split_title_with_space.extend(split_title_with_extra)
    
    # 合并所有有效标题片段并去重
    all_titles = original_title_base_list + split_title_with_space
    original_title_list = list(dict.fromkeys(all_titles))
    
    # 返回两个列表
    return original_title_base_list, original_title_list

def has_common_substring(title1, title2, length=5):
    # 检查两个字符串的长度是否至少为length
    print(f"检查字符串匹配情况:\ntitle1: {title1}\ntitle2: {title2}\nlength: {length}")
    if len(title1) < length or len(title2) < length:
        return False
    # 生成a的所有长度为length的连续子串，并检查是否存在于b中
    for i in range(len(title1) - length + 1):
        substring = title1[i:i+length]
        if substring in title2:
            return True
    return False

def check_detail_page(json_data, original_title_list, title_match_ele, actor_amazon):
    """
    获取amazon的详情页, 检测演员名是否匹配, 发行日期是否吻合
    """
    detail_url = title_match_ele[1]
    try:
        url_new = "https://www.amazon.co.jp" + re.findall(r"(/dp/[^/]+)", detail_url)[0]
    except:
        
        url_new = detail_url
    print(f"详情页url: {url_new}")
    result, html_detail = get_amazon_data(url_new)
    if result and html_detail:
        html = etree.fromstring(html_detail, etree.HTMLParser())
        # 获取演员名
        detail_actor = str(html.xpath('//span[@class="author notFaded"]/a/text()')).replace(" ", "")
        detail_info_1 = str(
            html.xpath('//ul[@class="a-unordered-list a-vertical a-spacing-mini"]//text()')
        ).replace(" ", "")
        detail_info_2 = str(
            html.xpath('//div[@id="detailBulletsWrapper_feature_div"]//text()')
        ).replace(" ", "")
        detail_info_3 = str(html.xpath('//div[@id="productDescription"]//text()')).replace(" ", "")
        all_info = detail_actor + detail_info_1 + detail_info_2 + detail_info_3
        # 获取发行日期
        date_text = html.xpath("//span[contains(text(), '発売日')]/following-sibling::span[1]/text()")
        release_amazon = date_text[0].strip() if date_text else "1970/1/1"
        release_date_amazon = datetime.strptime(release_amazon, "%Y/%m/%d")
        release_date = datetime.strptime(json_data.get('release'), "%Y-%m-%d")
        print(f"详情页日期：{release_date_amazon.strftime('%Y-%m-%d')}")
        print(f"release date: {json_data.get('release')}'")
        if (abs((release_date_amazon - release_date).days) < 30
            or release_amazon == "1970/1/1"
            or not release_date
        ):
            for each_actor in actor_amazon:
                if each_actor in all_info:
                    print(f"详情页匹配到演员: {each_actor}")
                    return True, True
            print(f"详情页未匹配到演员")
            
            for each_title in original_title_list:
                detail_page_title = title_match_ele[2]
                if has_common_substring(each_title, detail_page_title, 20): # 标题匹配达到20个字符
                    print(f"标题匹配度高")
                    return False, True
            print(f"标题匹配度低")
        else:
            print(f"发行日期不匹配")
            return False, False
    return False, False

def get_big_pic_by_amazon(json_data, originaltitle_amazon, actor_amazon):
    if not originaltitle_amazon or not actor_amazon:
        return ""
    hd_pic_url = ""
    actor_amazon, amazon_orginaltitle_actor = get_actor_list(json_data, originaltitle_amazon, actor_amazon)

    # 拆分标题
    originaltitle_amazon_base_list, originaltitle_amazon_list = split_title(originaltitle_amazon, actor_amazon, " ", "…")
    # 图片url过滤集合, 命中直接跳过
    pic_url_filtered_set = set()
    # 标题过滤集合, 命中直接跳过
    pic_title_filtered_set = set()
    # 去除标点空格, 全角转半角后的标题列表
    originaltitle_amazon_halfwidth_no_actor_base_list = []
    for each_title in originaltitle_amazon_base_list:
        originaltitle_amazon_halfwidth_no_actor_base_list.append(get_halfwidth_no_actor_title(each_title, actor_amazon, operation_flags=0b110)[1])
    print(f"去除标点空格和演员名, 全角转半角后的标题列表: {originaltitle_amazon_halfwidth_no_actor_base_list}")
    # 搜索标题
    for originaltitle_amazon in originaltitle_amazon_list:
        print(f"\n/********************开始搜索************************/")
        print(f"标题列表: {originaltitle_amazon_list}")
        print(f"搜索标题: {originaltitle_amazon}")
        print(f"图片url过滤集合: pic_url_filtered_set = {pic_url_filtered_set}") 
        print(f"标题过滤集合: pic_title_filtered_set = {pic_title_filtered_set}")
        # 需要两次urlencode，nb_sb_noss表示无推荐来源
        url_search = (
            "https://www.amazon.co.jp/black-curtain/save-eligibility/black-curtain?returnUrl=/s?k="
            + urllib.parse.quote_plus(urllib.parse.quote_plus(originaltitle_amazon.replace("&", " ") + " [DVD]"))
            + "&ref=nb_sb_noss"
        )
        result, html_search = get_amazon_data(url_search)

        if result and html_search:
            # 无结果直接跳过
            if "検索に一致する商品はありませんでした。" in html_search:
                print(f"无搜索结果, 结束本次搜索\n")
                continue
            html = etree.fromstring(html_search, etree.HTMLParser())
            originaltitle_amazon_halfwidth, originaltitle_amazon_halfwidth_no_actor = get_halfwidth_no_actor_title(originaltitle_amazon, actor_amazon)
            # 检查搜索结果
            title_match_list = []
            # s-card-container s-overflow-hidden aok-relative puis-wide-grid-style puis-wide-grid-style-t2 puis-expand-height puis-include-content-margin puis s-latency-cf-section s-card-border
            pic_card = html.xpath('//div[@class="a-section a-spacing-base"]')
            print(f"找到{len(pic_card)}个结果")
            
            # 开始处理搜索结果, 如果结果过多, 只处理前20个结果
            for each in pic_card[:20]:  # tek-077
                pic_ver_list = each.xpath(
                    'div//a[@class="a-size-base a-link-normal s-underline-text s-underline-link-text s-link-style a-text-bold"]/text()'
                )
                pic_title_list = each.xpath(
                    'div//h2[@class="a-size-base-plus a-spacing-none a-color-base a-text-normal"]/span/text()'
                )
                pic_url_list = each.xpath('div//div[@class="a-section aok-relative s-image-square-aspect"]/img/@src')
                detail_url_list = each.xpath('div//a[@class="a-link-normal s-no-outline"]/@href')
                
                if len(pic_ver_list) and len(pic_url_list) and (len(pic_title_list) and len(detail_url_list)):
                    pic_ver = pic_ver_list[0]  # 图片版本
                    pic_title = pic_title_list[0]  # 图片标题
                    pic_url = pic_url_list[0]  # 图片链接
                    pic_trunc_url = re.sub(r"\._[_]?AC_[^\.]+\.", ".", pic_url) # 去除后缀以获得更高分辨率的图片
                    detail_url = detail_url_list[0]  # 详情页链接（有时带有演员名）

                    # 去除非 DVD与无图片的结果
                    if (pic_ver not in ["DVD", "Software Download"]
                        or ".jpg" not in pic_trunc_url
                    ):
                        print(f"\n无效标题, 跳过: {pic_title}")
                        print(f"添加到过滤集合")
                        pic_title_filtered_set.add(pic_title)
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    
                    if pic_title in pic_title_filtered_set:
                        print(f"\n跳过已过滤的标题: {pic_title}")
                        continue
                    
                    w, h = get_imgsize(pic_trunc_url)
                    if w < 700 or w >= h:
                        print(f"\n图片非高清或非竖版, 跳过: {pic_trunc_url}")
                        print(f"添加到过滤集合")
                        pic_title_filtered_set.add(pic_title)
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    
                    # 避免单体作品取到合集结果 GVH-435
                    collection_keywords = ['BEST', '時間', '総集編', '完全', '枚組']
                    skip_flag = False
                    for collection_keyword in collection_keywords:
                        contains_s1 = collection_keyword in str(originaltitle_amazon_halfwidth_no_actor_base_list[0]).upper()
                        contains_s2 = collection_keyword in str(pic_title).upper()
                        if contains_s1 != contains_s2:
                            skip_flag = True
                    if skip_flag:
                        print(f"\n合集标题, 跳过: {pic_title}")
                        print(f"添加到过滤集合")
                        pic_title_filtered_set.add(pic_title)
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    print(f"\n+++++++++++++++++++++++++检测有效链接+++++++++++++++++++++++++")
                    print(f"搜索标题: {originaltitle_amazon}")
                    print(f"链接内容:\npic_ver = {pic_ver}\npic_title = {pic_title}\npic_trunc_url = {pic_trunc_url}")
                    pic_title_halfwidth = convert_half(re.sub(r"【.*】", "", pic_title))
                    if pic_trunc_url in pic_url_filtered_set:
                        print(f"\n跳过已过滤的图片url: {pic_trunc_url}")
                        continue
                    print(f"pic_title去除演员名:")
                    print(f"pic_title_halfwidth = {pic_title_halfwidth}")
                    for each_actor in actor_amazon:
                        pic_title_halfwidth = pic_title_halfwidth.replace(each_actor, "")
                    pic_title_halfwidth_no_actor = pic_title_halfwidth.strip()
                    print(f"pic_title_halfwidth_no_actor = {pic_title_halfwidth_no_actor}\n")

                    # 判断标题是否命中
                    if (
                        originaltitle_amazon_halfwidth[:15] in pic_title_halfwidth
                        or originaltitle_amazon_halfwidth_no_actor[:15] in pic_title_halfwidth_no_actor
                        or has_common_substring(originaltitle_amazon_halfwidth,pic_title_halfwidth)
                        or has_common_substring(originaltitle_amazon_halfwidth_no_actor,pic_title_halfwidth_no_actor)
                    ):
                        print(f"命中标题:\noriginaltitle_amazon_halfwidth = {originaltitle_amazon_halfwidth}\npic_title_halfwidth = {pic_title_halfwidth}")
                        print(f"originaltitle_amazon_halfwidth_no_actor = {originaltitle_amazon_halfwidth_no_actor}\npic_title_halfwidth_no_actor = {pic_title_halfwidth_no_actor}\n")
                        detail_url = urllib.parse.unquote_plus(detail_url)
                        temp_title = re.findall(r"(.+)keywords=", detail_url)
                        temp_detail_url = (
                            temp_title[0] + pic_title_halfwidth if temp_title else detail_url + pic_title_halfwidth
                        )
                        detail_url_full = "https://www.amazon.co.jp" + detail_url

                        # 判断演员是否在标题里，避免同名标题误匹配 MOPP-023
                        for each_actor in actor_amazon:
                            if each_actor in temp_detail_url:
                                print(f"命中演员: {each_actor}")
                                print(f"采用此结果")
                                hd_pic_url = pic_trunc_url
                                return hd_pic_url
                        else:
                            # 如果没有命中任何演员，添加到 title_match_list
                            print(f"没有命中演员, 添加到 title_match_list")
                            title_match_list.append([pic_trunc_url, detail_url_full, pic_title_halfwidth_no_actor])
                            print(f"title_match_list_pic_only = {[element[0] for element in title_match_list]}")
                    else:
                        print(f"标题未命中, 跳过: {pic_title}")
                        print(f"添加到过滤集合")
                        pic_title_filtered_set.add(pic_title)
                        pic_url_filtered_set.add(pic_trunc_url)
                else:
                    print(f"\n跳过不包含类型, 图片, 标题, 详情页面的结果")
                    pass
                    
            # 当搜索结果命中了标题，没有命中演员时，尝试去详情页获取演员信息
            if (
                len(title_match_list) > 0
                and len(title_match_list) <= 20
                and "s-pagination-item s-pagination-next s-pagination-button s-pagination-button-accessibility s-pagination-separator" not in html_search
            ):
                print(f"尝试去详情页获取演员信息")
                # 检测前4个结果
                title_match_pic_list = []
                for each in title_match_list[:4]:
                    actor_match, title_match =  check_detail_page(json_data, originaltitle_amazon_halfwidth_no_actor_base_list, each, actor_amazon)
                    if actor_match:
                        print(f"详情页检测通过, 采用此结果")
                        return each[0]
                    elif title_match:
                        title_match_pic_list.append(each[0])
                if len(title_match_pic_list) > 0:
                    print(f"采用第一个标题匹配度高的结果")
                    return title_match_pic_list[0]
                else:
                    print(f"详情页检测未通过, 添加到过滤集合")
                    print(f"详情页标题: pic_title = {pic_title}")
                    pic_url_filtered_set.add(each[0])
                    pic_title_filtered_set.add(pic_title)
                    
            # 有很多结果时（有下一页按钮），加演员名字重新搜索
            if (
                "s-pagination-item s-pagination-next s-pagination-button s-pagination-button-accessibility s-pagination-separator" in html_search
                or len(title_match_list) > 5
            ):
                if amazon_orginaltitle_actor:
                    for actor in amazon_orginaltitle_actor:
                        if not (actor in originaltitle_amazon):
                            originaltitle_amazon_list.extend([originaltitle_amazon + ' ' + actor])
                    print(f"\n添加演员名再次搜索")

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

    # 勾选复制 thumb时：国产，复制thumb；无码，勾选不裁剪时，也复制thumb
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

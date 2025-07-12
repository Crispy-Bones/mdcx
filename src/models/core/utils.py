"""
刮削过程的一般工具函数
依赖:
    此模块不应依赖 models.core 中除 flags 外的任何其他模块
"""

import os
import re
import traceback

import cv2
import unicodedata

from models.base.file import read_link, split_path
from models.base.number import get_number_letters
from models.base.path import get_main_path, get_path
from models.base.utils import convert_path, get_used_time
from models.config.config import config
from models.config.resources import resources
from models.signals import signal
from itertools import product
from collections import defaultdict

def replace_word(json_data):
    # 常见字段替换的字符
    for key, value in config.all_rep_word.items():
        for each in config.all_key_word:
            json_data[each] = json_data[each].replace(key, value)

    # 简体时替换的字符
    key_word = []
    if config.title_language == "zh_cn":
        key_word.append("title")
    if config.outline_language == "zh_cn":
        key_word.append("outline")

    for key, value in config.chinese_rep_word.items():
        for each in key_word:
            json_data[each] = json_data[each].replace(key, value)

    # 替换标题的上下集信息
    fields_word = ["title", "originaltitle"]
    for field in fields_word:
        for each in config.title_rep:
            json_data[field] = json_data[field].replace(each, "").strip(":， ").strip()


def show_movie_info(json_data):
    if config.show_data_log == "off":  # 调试模式打开时显示详细日志
        return
    for key in config.show_key:
        value = json_data.get(key)
        if not value:
            continue
        if key == "outline" or key == "originalplot" and len(value) > 100:
            value = str(value)[:98] + "……（略）"
        elif key == "has_sub":
            value = "中文字幕"
        elif key == "actor" and "actor_all," in config.nfo_include_new:
            value = json_data["all_actor"]
        json_data["logs"] += "\n     " + "%-13s" % key + ": " + str(value)


def get_video_size(json_data, file_path):
    # 获取本地分辨率 同时获取视频编码格式
    definition = ""
    height = 0
    hd_get = config.hd_get
    if os.path.islink(file_path):
        if "symlink_definition" in config.no_escape:
            file_path = read_link(file_path)
        else:
            hd_get = "path"
    if hd_get == "video":
        try:
            cap = cv2.VideoCapture(file_path)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            ##使用opencv获取编码器格式
            codec = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec_fourcc = (
                chr(codec & 0xFF) + chr((codec >> 8) & 0xFF) + chr((codec >> 16) & 0xFF) + chr((codec >> 24) & 0xFF)
            )

        except Exception as e:
            signal.show_traceback_log(traceback.format_exc())
            signal.show_traceback_log(str(e))
            signal.show_log_text(traceback.format_exc())
            signal.show_log_text(f" 🔴 无法获取视频分辨率！ 文件地址: {file_path}  错误信息: {e}")
    elif hd_get == "path":
        file_path_temp = file_path.upper()
        if "8K" in file_path_temp:
            height = 4000
        elif "4K" in file_path_temp or "UHD" in file_path_temp:
            height = 2000
        elif "1440P" in file_path_temp or "QHD" in file_path_temp:
            height = 1440
        elif "1080P" in file_path_temp or "FHD" in file_path_temp:
            height = 1080
        elif "960P" in file_path_temp:
            height = 960
        elif "720P" in file_path_temp or "HD" in file_path_temp:
            height = 720

    hd_name = config.hd_name
    if not height:
        pass
    elif height >= 4000:
        definition = "8K" if hd_name == "height" else "UHD8"
    elif height >= 2000:
        definition = "4K" if hd_name == "height" else "UHD"
    elif height >= 1400:
        definition = "1440P" if hd_name == "height" else "QHD"
    elif height >= 1000:
        definition = "1080P" if hd_name == "height" else "FHD"
    elif height >= 900:
        definition = "960P" if hd_name == "height" else "HD"
    elif height >= 700:
        definition = "720P" if hd_name == "height" else "HD"
    elif height >= 500:
        definition = "540P" if hd_name == "height" else "qHD"
    elif height >= 400:
        definition = "480P"
    elif height >= 300:
        definition = "360P"
    elif height >= 100:
        definition = "144P"
    json_data["definition"] = definition

    if definition in ["4K", "8K", "UHD", "UHD8"]:
        json_data["4K"] = "-" + definition

    # 去除标签中的分辨率率，使用本地读取的实际分辨率
    remove_key = ["144P", "360P", "480P", "540P", "720P", "960P", "1080P", "1440P", "2160P", "4K", "8K"]
    tag = json_data["tag"]
    for each_key in remove_key:
        tag = tag.replace(each_key, "").replace(each_key.lower(), "")
    tag_list = re.split(r"[,，]", tag)
    new_tag_list = []
    [new_tag_list.append(i) for i in tag_list if i]
    if definition and "definition" in config.tag_include:
        new_tag_list.insert(0, definition)
        if hd_get == "video":
            new_tag_list.insert(0, codec_fourcc.upper())  # 插入编码格式
    json_data["tag"] = "，".join(new_tag_list)
    return json_data


def show_data_result(json_data, start_time):
    if json_data["error_info"] or json_data["title"] == "":
        json_data["logs"] += (
            "\n 🌐 [website] %s" % json_data["req_web"].strip("-> ")
            + "\n"
            + json_data["log_info"].strip(" ").strip("\n")
            + "\n"
            + " 🔴 Data failed!(%ss)" % (get_used_time(start_time))
        )
        return False
    else:
        if config.show_web_log == "on":  # 字段刮削过程
            json_data["logs"] += "\n 🌐 [website] %s" % json_data["req_web"].strip("-> ")
        try:
            if json_data["log_info"]:
                json_data["logs"] += "\n" + json_data["log_info"].strip(" ").strip("\n")
        except:
            signal.show_log_text(traceback.format_exc())
        if config.show_from_log == "on":  # 字段来源信息
            if json_data["fields_info"]:
                json_data["logs"] += "\n" + json_data["fields_info"].strip(" ").strip("\n")
        json_data["logs"] += "\n 🍀 Data done!(%ss)" % (get_used_time(start_time))
        return True


def deal_url(url):
    if "://" not in url:
        url = "https://" + url
    url = url.strip()
    for key, vlaue in config.web_dic.items():
        if key.lower() in url.lower():
            return vlaue, url

    # 自定义的网址
    for web_name in config.SUPPORTED_WEBSITES:
        if hasattr(config, web_name + "_website"):
            web_url = getattr(config, web_name + "_website")
            if web_url in url:
                return web_name, url

    return False, url


def replace_special_word(json_data):
    # 常见字段替换的字符
    all_key_word = [
        "title",
        "originaltitle",
        "outline",
        "originalplot",
        "series",
        "director",
        "studio",
        "publisher",
        "tag",
    ]
    for key, value in config.special_word.items():
        for each in all_key_word:
            json_data[each] = json_data[each].replace(key, value[0])
    
def convert_half(string, operation_flags=0b111):
    """
    多阶段文本处理函数，支持敏感词替换、字符格式转换和清理
    
    主要功能：
    1. 敏感词替换：根据配置替换包含屏蔽符号(●/○)的词汇，支持双向映射
    2. 全角转半角：转换配置指定的全角字符为半角
    3. 格式清理：移除非单词字符并转为大写
    
    参数：
    string (str): 待处理的原始字符串
    operation_flags (int): 位标志控制处理流程
        - 0b001 (1): 执行敏感词替换
        - 0b010 (2): 执行全角转半角
        - 0b100 (4): 执行格式清理和大写转换
    
    返回：
    list: 处理后的字符串列表，按未屏蔽版本优先排序，保持唯一性
    """
    
    def generate_normalized_mapping(special_word):
        """
        生成标准化映射字典，用于全处理模式下的字符替换
        处理规则：将包含●/○的键映射到去掉对应位置字符的值
        示例："強●" => ("強制","強姦") 会生成映射："強●"->"強", "強制"->"強", "強姦"->"強"
        """
        normalized = defaultdict(set)
        for key, values in special_word.items():
            for v in values:
                # 仅处理包含屏蔽符号的键
                if "●" in key or "○" in key:
                    # 计算屏蔽符号位置
                    symbol = "●" if "●" in key else "○"
                    index = key.index(symbol)
                    # 生成标准化结果（去掉对应位置字符）
                    normalized_target = v[:index] + v[index+1:] if index < len(v) else v
                    # 建立双向映射
                    normalized[key].add(normalized_target)
                    normalized[v].add(normalized_target)
        return {k: list(v) for k, v in normalized.items()}

    # 初始化结果集合，保留原始字符串
    results = {string}

    # ========================
    # 阶段1：敏感词替换处理
    # ========================
    if operation_flags & 0b001:
        # 构建反向映射字典（标准词 -> 所有可能的屏蔽形式）
        reverse_special_word = defaultdict(list)
        for key, values in config.special_word.items():
            targets = values if isinstance(values, (tuple, list)) else [values]
            for target in targets:
                reverse_special_word[target].append(key)

        # 合并正向和反向映射，构建完整替换字典
        all_replacements = defaultdict(tuple)
        # 添加正向映射
        for key, values in config.special_word.items():
            targets = values if isinstance(values, (tuple, list)) else [values]
            all_replacements[key] = tuple(targets)
        # 合并反向映射
        for key, sources in reverse_special_word.items():
            existing = list(all_replacements.get(key, ()))
            existing.extend(sources)
            all_replacements[key] = tuple(set(existing))  # 去重

        # 根据处理模式选择映射表
        replacements = (
            generate_normalized_mapping(all_replacements) 
            if (operation_flags & 0b100)  # 全处理模式需要标准化映射
            else {k: list(v) for k, v in all_replacements.items()}
        )

        # 预处理：统一○为●以便匹配
        processed_str = string.replace("○", "●")
        # 按长度降序排序模式，确保长模式优先匹配
        sorted_patterns = sorted(replacements.keys(), key=lambda x: (-len(x), x))
        pattern = re.compile("|".join(map(re.escape, sorted_patterns)))
        matches = pattern.finditer(processed_str)

        if matches:
            # 将匹配项按原始模式分组（相同模式的不同匹配归为一组）
            pattern_groups = defaultdict(list)
            for m in matches:
                matched_pattern = m.group()
                pattern_groups[matched_pattern].append(m)

            # 初始化替换选项字典
            filter_special = (operation_flags == 0b111)  # 全处理模式需要过滤屏蔽符号
            replacement_options = {}
            patterns = list(pattern_groups.keys())  # 获取唯一模式列表

            # 判断是否需要添加原词选项（混合存在屏蔽词和未屏蔽词时才需要）
            has_shielded = any(p in config.special_word for p in patterns)
            has_unshielded = any(p not in config.special_word for p in patterns)
            need_extra = has_shielded and has_unshielded and (operation_flags != 0b111)

            # 为每个模式生成替换选项
            for pattern in patterns:
                candidates = replacements.get(pattern, [])
                # 根据处理模式过滤候选词
                filtered = [
                    repl for repl in candidates
                    if not filter_special or ("●" not in repl and "○" not in repl)
                ]
                
                # 添加原词选项的条件处理（非全处理模式且当前模式无屏蔽符号）
                if need_extra and ("●" not in pattern and "○" not in pattern):
                    if pattern not in filtered:
                        filtered.append(pattern)
                
                replacement_options[pattern] = filtered

            # 生成所有可能的替换组合（笛卡尔积）
            results = set()
            for combo in product(*[replacement_options[p] for p in patterns]):
                temp_str = string  # 使用原始字符串以保留○符号
                replace_actions = []
                
                # 收集所有替换操作
                for pattern_idx, chosen_repl in enumerate(combo):
                    current_pattern = patterns[pattern_idx]
                    for m in pattern_groups[current_pattern]:
                        # 记录替换位置和内容
                        replace_actions.append( (m.start(), m.end(), chosen_repl) )
                
                # 按倒序执行替换，避免位置偏移
                replace_actions.sort(reverse=True, key=lambda x: x[0])
                for start, end, repl in replace_actions:
                    temp_str = temp_str[:start] + repl + temp_str[end:]
                
                results.add(temp_str)

            # 结果排序：未屏蔽版本优先
            results = sorted(
                list(results),
                key=lambda s: any(c in s for c in ('●', '○'))  # 包含屏蔽符号的排在后面
            )
        else:
            results = [string]  # 无匹配时返回原始字符串

    # ========================
    # 阶段2：全角转半角处理
    # ========================
    conver_list = list(results)
    if operation_flags & 0b010:
        for i in range(len(conver_list)):
            s = conver_list[i]
            for full, half in config.full_half_char:
                s = s.replace(full, half)
            conver_list[i] = s

    # ========================
    # 阶段3：格式清理和大写转换
    # ========================
    if operation_flags & 0b100:
        conver_list = [
            # 移除非单词字符（保留字母、数字、汉字）并转为大写
            re.sub(r"[\W_]+", "", s).upper()
            for s in conver_list
        ]

    # ========================
    # 最终处理：去重并保持顺序
    # ========================
    seen = set()
    final_results = []
    for s in conver_list:
        if s and (s not in seen):  # 过滤空字符串并去重
            seen.add(s)
            final_results.append(s)
    
    return final_results


def get_new_release(release):
    release_rule = config.release_rule
    if not release:
        release = "0000-00-00"
    if release_rule == "YYYY-MM-DD":
        return release
    year, month, day = re.findall(r"(\d{4})-(\d{2})-(\d{2})", release)[0]
    return release_rule.replace("YYYY", year).replace("YY", year[-2:]).replace("MM", month).replace("DD", day)


def nfd2c(path):
    # 转换 NFC(mac nfc和nfd都能访问到文件，但是显示的是nfd，这里统一使用nfc，避免各种问题。
    # 日文浊音转换（mac的坑，osx10.12以下使用nfd，以上兼容nfc和nfd，只是显示成了nfd）
    if config.is_nfc:
        new_path = unicodedata.normalize("NFC", path)  # Mac 会拆成两个字符，即 NFD，windwos是 NFC
    else:
        new_path = unicodedata.normalize("NFD", path)  # Mac 会拆成两个字符，即 NFD，windwos是 NFC
    return new_path


def deal_some_field(json_data):
    fields_rule = config.fields_rule
    actor = json_data["actor"]
    title = json_data["title"]
    originaltitle = json_data["originaltitle"]
    number = json_data["number"]

    # 演员处理
    if actor:
        # 去除演员名中的括号
        new_actor_list = []
        actor_list = []
        temp_actor_list = []
        for each_actor in actor.split(","):
            if each_actor and each_actor not in actor_list:
                actor_list.append(each_actor)
                new_actor = re.findall(r"[^\(\)\（\）]+", each_actor)
                if new_actor[0] not in new_actor_list:
                    new_actor_list.append(new_actor[0])
                temp_actor_list.extend(new_actor)
        if "del_char" in fields_rule:
            json_data["actor"] = ",".join(new_actor_list)
        else:
            json_data["actor"] = ",".join(actor_list)

        # 去除标题后的演员名
        if "del_actor" in fields_rule:
            new_all_actor_name_list = []
            for each_actor in json_data["actor_amazon"] + temp_actor_list:
                actor_keyword_list = resources.get_actor_data(each_actor).get(
                    "keyword"
                )  # 获取演员映射表的所有演员别名进行替换
                new_all_actor_name_list.extend(actor_keyword_list)
            for each_actor in set(new_all_actor_name_list):
                try:
                    end_actor = re.compile(r" %s$" % each_actor)
                    title = re.sub(end_actor, "", title)
                    originaltitle = re.sub(end_actor, "", originaltitle)
                except:
                    signal.show_traceback_log(traceback.format_exc())
        json_data["title"] = title.strip()
        json_data["originaltitle"] = originaltitle.strip()

    # 去除标题中的番号
    if number != title and title.startswith(number):
        title = title.replace(number, "").strip()
        json_data["title"] = title
    if number != originaltitle and originaltitle.startswith(number):
        originaltitle = originaltitle.replace(number, "").strip()
        json_data["originaltitle"] = originaltitle

    # 去除标题中的/
    json_data["title"] = json_data["title"].replace("/", "#").strip(" -")
    json_data["originaltitle"] = json_data["originaltitle"].replace("/", "#").strip(" -")

    # 去除素人番号前缀数字
    if "del_num" in fields_rule:
        temp_n = re.findall(r"\d{3,}([a-zA-Z]+-\d+)", number)
        if temp_n:
            json_data["number"] = temp_n[0]
            json_data["letters"] = get_number_letters(json_data["number"])

    if number.endswith("Z"):
        json_data["number"] = json_data["number"][:-1] + "z"
    return json_data


def get_movie_path_setting(file_path=""):
    # 先把'\'转成'/'以便判断是路径还是目录
    movie_path = config.media_path.replace("\\", "/")  # 用户设置的扫描媒体路径
    if movie_path == "":  # 未设置为空时，使用主程序目录
        movie_path = get_main_path()
    movie_path = nfd2c(movie_path)
    end_folder_name = split_path(movie_path)[1]
    # 用户设置的软链接输出目录
    softlink_path = config.softlink_path.replace("\\", "/").replace("end_folder_name", end_folder_name)
    # 用户设置的成功输出目录
    success_folder = config.success_output_folder.replace("\\", "/").replace("end_folder_name", end_folder_name)
    # 用户设置的失败输出目录
    failed_folder = config.failed_output_folder.replace("\\", "/").replace("end_folder_name", end_folder_name)
    # 用户设置的排除目录
    escape_folder_list = (
        config.folders.replace("\\", "/").replace("end_folder_name", end_folder_name).replace("，", ",").split(",")
    )
    # 用户设置的剧照副本目录
    extrafanart_folder = config.extrafanart_folder.replace("\\", "/")

    # 获取路径
    softlink_path = convert_path(get_path(movie_path, softlink_path))
    success_folder = convert_path(get_path(movie_path, success_folder))
    failed_folder = convert_path(get_path(movie_path, failed_folder))
    softlink_path = nfd2c(softlink_path)
    success_folder = nfd2c(success_folder)
    failed_folder = nfd2c(failed_folder)
    extrafanart_folder = nfd2c(extrafanart_folder)

    # 获取排除目录完整路径（尾巴添加/）
    escape_folder_new_list = []
    for es in escape_folder_list:  # 排除目录可以多个，以，,分割
        es = es.strip(" ")
        if es:
            es = get_path(movie_path, es).replace("\\", "/")
            if es[-1] != "/":  # 路径尾部添加“/”，方便后面move_list查找时匹配路径
                es += "/"
            es = nfd2c(es)
            escape_folder_new_list.append(es)

    if file_path:
        temp_path = movie_path
        if config.scrape_softlink_path:
            temp_path = softlink_path
        if "first_folder_name" in success_folder or "first_folder_name" in failed_folder:
            first_folder_name = re.findall(r"^/?([^/]+)/", file_path[len(temp_path) :].replace("\\", "/"))
            first_folder_name = first_folder_name[0] if first_folder_name else ""
            success_folder = success_folder.replace("first_folder_name", first_folder_name)
            failed_folder = failed_folder.replace("first_folder_name", first_folder_name)

    return (
        convert_path(movie_path),
        success_folder,
        failed_folder,
        escape_folder_new_list,
        extrafanart_folder,
        softlink_path,
    )

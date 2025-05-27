#!/usr/bin/env python3
"""
功能:
从日本Amazon官网刮削高清封面

简介:
Amazon搜索时使用的是影片标题而非番号, 而同一影片在不同平台的标题会有差异, 尤其Amazon的很多影片有自己的命名规则, 因此需要单独进行处理

步骤说明:
1. 生成演员列表 - 处理入参的演员列表, 去除可能的括号, 并整合去重, 同时返回最符合的演员名;
2. 生成搜索标题 - 根据不同的分隔符拆分入参标题, 在此基础上额外生成添加演员名的列表, 最后合并;
3. 获取搜索结果 - 遍历搜索标题列表进行搜索, 获取相关html信息;
4. 生成匹配标题 - 处理搜索标题和Amazon标题, 用于匹配;
5. 进行标题匹配 - 根据既定规则进行匹配;
6. 进行演员匹配 - 检测Amazon标题的演员与实际演员是否一致, 一致则采用, 无法确定时继续检测;
7. 详情页面匹配 - 检测详情页的演员名与发行日期, 一致则采用, 无法确定时继续检测;
8. 选取可能结果 - 以上流程均无法确定时, 选取最可能的结果;

附加说明:
1. 脚本会频繁访问Amazon页面, 请求过多时会报错, 建议减少并行刮削数量;
2. 对于存在dmm高清横版和竖版封面的影片, 将不再搜索Amazon, 而是直接采用dmm的竖版封面, 这也能减少对Amazon的访问;
3. 如果匹配到Amazon影片, 但无高清封面, 则不会使用, 也不会以此进行Google搜图;
4. 不能保证100%匹配率, 对于主流厂商, 在与dmm结果相结合的情况下, 匹配度可达95%以上;
"""
import re
import urllib
import math

from lxml import etree

from models.base.web import get_amazon_data, get_imgsize
from models.core.utils import convert_half
from models.config.config import config
from datetime import datetime
import time

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
            name_before_bracket = match.group(1).strip().upper()  # 括号前的内容
            name_in_bracket = match.group(2).strip().upper()    # 括号内的内容
            
            # 将拆分后的两个部分添加到集合中
            actor_list.append(name_before_bracket)
            actor_list.append(name_in_bracket)
        else:
            # 如果没有括号，直接添加到集合中
            actor_list.append(item.strip().upper())

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
    print(f"\n开始生成演员列表...")
    print(f"原始标题: {title}")
    print(f"原始演员列表: {raw_actor_list}")
    
    raw_actor_in_title = json_data.get("amazon_orginaltitle_actor")
    print(f"刮削数据中的演员: {raw_actor_in_title}")
    
    
    # 调用 _split_actor 函数处理演员列表
    actor_list = _split_actor(raw_actor_list) if raw_actor_list else []
    actor_in_title_list = _split_actor([raw_actor_in_title]) if raw_actor_in_title else []
    
    # 将标题中的演员名放在首位
    combined_actor_list = actor_in_title_list + actor_list
    
    if combined_actor_list:
        for actor in combined_actor_list:
            # 如果标题中包含演员名, 则放置首位
            if actor in title.upper():
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

def _remove_actor(title, actor_list):
    """
    删除title末尾的所有存在于actor_list中的演员名(支持连续多个演员名), 保留标题中间的演员名
    """
    while True:
        removed = False  # 标记是否成功删除了演员名
        for actor in actor_list:
            # 确保actor不为空，并且标题以该演员名结尾
            if actor and title.endswith(actor):
                title = title[:-len(actor)]
                # 删除多个演员名之间的符号, 保留字母, 数字, 中日文与'●|○', MEYD-012
                title = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff●○]+$", "", title)
                removed = True  # 标记已删除演员名
                break  # 重新检查新的标题末尾
        
        # 如果没有删除任何演员名，则退出循环
        if not removed:
            break
    return title
def _add_actor_to_title(title_list, best_match_actor, seq_flag="no actor first"):
    """
    将最符合的演员名添加到标题末尾，并按指定顺序交错排列。

    参数:
        title_list (list): 原始标题列表。
        best_match_actor (str): 最匹配的演员名。
        seq_flag (str): 控制交错排列顺序。可选值为 'actor first' 或 'no actor first'。
                         默认为 'no actor first'。

    返回:
        list: 包含原始标题和带演员名标题的交错列表。
    """
    # 如果演员名为空或无效，直接返回原始标题列表
    if not best_match_actor:
        return title_list

    # 根据 seq_flag 决定排列顺序
    search_title_list = []
    for title in title_list:
        title_with_actor = f"{title} {best_match_actor}"
        if seq_flag == "actor first":
            search_title_list.append(title_with_actor)  # 带演员名的标题在前
            search_title_list.append(title)             # 原始标题在后
        elif seq_flag == "no actor first":
            search_title_list.append(title)             # 原始标题在前
            search_title_list.append(title_with_actor)  # 带演员名的标题在后
        else:
            raise ValueError("Invalid value for seq_flag. Must be 'actor first' or 'no actor first'.")

    return search_title_list


def _split_title(
                original_title,
                actor_list,
                best_match_actor,
                min_length=3,
                pattern=None,
                separator=r"\s+",
                extra_separator=None
                ):
    """
    入参 original_title 为原始标题并且去除了末尾的演员名, 将其拆分整合用作Amazon搜索
    1. 正则匹配标题中的 pattern 并移除;
    2. 移除标题末尾所有的演员名, 保留标题中间的演员名;
    3. 对标题进行敏感词转换, 若转换后结果不同则加入结果列表;
    4. 如果标题长度不超过 min_length 则直接添加演员名;
    5. 构建分隔符正则表达式;
    6. 若原始标题不包含任何分隔符，则直接返回基础标题列表;
    7. 否则按主分隔符拆分并过滤无效子串;
    8. 若有额外分隔符，继续按其拆分;
    9. 最终返回原始标题与敏感词转换后的标题列表, 以及所有有效标题片段的去重列表。
    """
    if pattern:
        original_title = re.sub(pattern, "", original_title).strip()
    # 移除标题末尾所有的演员名, 保留标题中间的演员名;
    original_title = _remove_actor(original_title, actor_list)
    # 初始化原标题列表
    no_split_title_list = [original_title]
    
    # 敏感词转换
    original_title_convert_list = convert_half(original_title, operation_flags=0b001)
    for title in original_title_convert_list:
        if title != original_title:
            no_split_title_list.append(title)
    
    # 全半角转换
    fullwidth_list = ["ホールド", "ガチ"]
    for i in range(len(no_split_title_list)):
        for full, half in config.full_half_char:
            if full in fullwidth_list:
                no_split_title_list[i] = no_split_title_list[i].replace(full, half)
        
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
        return no_split_title_list[0], no_split_title_list, no_split_title_list

    # 整合主分隔符和额外分隔符
    if extra_separator is None:
        extra_separator = []
    # 构建完整的分隔符正则表达式
    sep_patterns = [separator] + extra_separator
    combined_pattern = "|".join(sep_patterns)
    # 编译正则表达式
    sep_regex = re.compile(combined_pattern)
    print(f"sep_regex = {sep_regex}")
    # 如果没有匹配到分隔符，直接返回基础标题列表
    match = sep_regex.search(no_split_title_list[0])
    if not match:
        print(f"标题无需分隔, 直接返回基础标题列表")
        # 根据最短标题长度选择排列顺序, 标题过短时优先搜索添加演员的标题
        seq_flag = "actor first" if len(no_split_title_list[0]) <= 15  else "no actor first"
        search_title_list = _add_actor_to_title(no_split_title_list, best_match_actor, seq_flag)
        if seq_flag == "actor first":
            no_split_title = search_title_list[0]
        else:
            no_split_title = search_title_list[1]
        return no_split_title, search_title_list, search_title_list
    
    def split_and_filter(title, no_split_title_length, actor_list, sep, length_ratio=0.15):
        """
        按照指定的分隔符拆分标题字符串，并根据规则过滤拆分片段
        入参:
            title (str): 要拆分的标题字符串
            no_split_title_length (int): 未拆分标题的长度
            actor_list (list): 演员列表，用于跳过包含演员名称的拆分片段
            sep (str): 分隔符，默认为 r'\s+'（匹配连续空白字符）
            length_ratio (float): 拆分片段的最小长度比例，默认为 0.15
        分隔规则:
        1. 连续多个分隔符视为一个分隔符
        2. 对于所有的拆分片段 part, len(part) > =4
        3. 对于所有的拆分片段 part, if part in actor_list, 则跳过此分隔字符, 检测下一个分隔
        4. 对于所有的拆分片段 part, 如果是字母数字混合, 或者为'字母-数字'格式, 则跳过此分隔字符, 检测下一个分隔
        5. 如果分隔符数量 >= 4, if len(part) < no_split_title_length * length_ratio, 则跳过此分隔, 检测下一个分隔
        6. 检测最后一个part, 满足规则时添加到拆分标题列表, 否则合并到上一个部分
        返回:
            list: 拆分后的标题列表
        """
        # 解析分隔符 sep，提取出单一连接符
        def get_separator_char(sep):
            # 特殊处理常见的转义字符
            escape_chars = {
                r"\s": " ",  # 空白字符对应空格
                r"\d": "0",  # 数字对应 '0'
                r"\w": "a",  # 字母或数字对应 'a'
            }
            # 去掉量词（如 '+'、'*'、'?'）
            core_sep = re.sub(r"[+*?]+$", "", sep)
            # 如果是转义字符，返回对应的单一字符
            if core_sep in escape_chars:
                return escape_chars[core_sep]
            # 如果是普通字符（如 '!'、','、'?'），直接返回第一个字符
            if core_sep:
                return core_sep[0]
            # 默认返回空格
            return " "
        
        # 使用正则表达式找到所有分隔符块的位置
        sep_blocks = [(m.start(), m.end()) for m in re.finditer(sep, title)]
        # 分隔符块数量
        sep_count = len(sep_blocks)
        # 结果列表
        result = []
        start = 0  # 当前拆分起始位置
        
        # 辅助函数：检查是否为字母数字混合或 '字母-数字' 格式
        def is_alphanumeric_or_pattern(part):
            return bool(re.match(r'^[A-Za-z0-9]+$', part)) or bool(re.match(r'^[A-Za-z]+-[0-9]+$', part))
        
        # 获取连接符
        separator_char = get_separator_char(sep)
        
        for i, (d_start, d_end) in enumerate(sep_blocks):
            # 拆分当前部分
            part = title[start:d_start]
            # 规则 1: 长度必须 >= 4
            if len(part) < 4:
                continue
            # 规则 2: 如果 part 在 actor_list 中，则跳过
            if part in actor_list:
                continue
            # 规则 3: 如果 part 是字母数字混合或符合 '字母-数字' 格式，则跳过
            if is_alphanumeric_or_pattern(part):
                continue
            # 规则 4: 如果分隔符数量 >= 4，则检测长度
            if sep_count >= 4 and len(part) < length_ratio * no_split_title_length:
                continue
            # 如果通过所有规则，则添加到结果列表中
            result.append(part)
            start = d_end  # 更新起始位置为当前分隔符块的结束位置
        
        # 添加最后一部分，并检查规则
        last_part = title[start:]
        if last_part:
            # 规则 1: 长度必须 >= 4
            if len(last_part) < 4:
                pass  # 不满足规则，标记为需要合并
            # 规则 2: 如果 last_part 在 actor_list 中，则跳过
            elif last_part in actor_list:
                pass  # 不满足规则，标记为需要合并
            # 规则 3: 如果 last_part 是字母数字混合或符合 '字母-数字' 格式，则跳过
            elif is_alphanumeric_or_pattern(last_part):
                pass  # 不满足规则，标记为需要合并
            # 规则 4: 如果分隔符数量 >= 4，则检测长度
            elif sep_count >= 4 and len(last_part) < length_ratio * no_split_title_length:
                pass  # 不满足规则，标记为需要合并
            else:
                # 如果通过所有规则，则直接添加到最后
                result.append(last_part)
                last_part = None  # 标记为已处理
        
        # 如果 last_part 未被处理（即不满足规则），合并到上一个部分
        if last_part:
            if not result:
                result.append(last_part)  # 如果结果为空，直接添加
            else:
                # 使用解析出的连接符进行合并
                result[-1] += separator_char + last_part.strip()
        return result
    
    # 原始标题长度
    no_split_title_length = len(no_split_title_list[0])
    
    # 先以空格拆分
    split_title_with_space = []
    for title in no_split_title_list:
        split_title_with_space.extend(split_and_filter(title, no_split_title_length, actor_list, separator))
    
    # 再以额外分隔符拆分
    if extra_separator:
        base_titles = split_title_with_space.copy() or no_split_title_list.copy()
        for extra in extra_separator:
            split_title_with_extra = []
            for title in base_titles:
                split_title_with_extra.extend(split_and_filter(title, no_split_title_length, actor_list, extra))
            # 将新分隔的结果合并到已有的标题列表中
            split_title_with_space.extend(split_title_with_extra)

    # 去除标题首尾的分隔符
    core_characters = [sep.rstrip('+') for sep in sep_patterns]
    pattern_str = f"({'|'.join(core_characters)})+"
    # 创建匹配标题首尾分隔符的正则表达式
    start_separators_pattern = re.compile(f"^{pattern_str}")
    end_separators_pattern = re.compile(f"{pattern_str}$")
    titles_no_actor = no_split_title_list + split_title_with_space
    for i in range(len(titles_no_actor)):
        titles_no_actor[i] = start_separators_pattern.sub("", titles_no_actor[i]) # 去掉开头分隔符
        titles_no_actor[i] = end_separators_pattern.sub("", titles_no_actor[i]) # 去掉末尾分隔符
    
    # 合并所有有效标题片段并去重
    titles_no_actor = list(dict.fromkeys(titles_no_actor))
    # 获取最短标题长度
    shortest_length = len(min(titles_no_actor, key=len))
    # 根据最短标题长度选择排列顺序, 标题过短时优先搜索添加演员的标题
    seq_flag = "actor first" if shortest_length <= 15  else "no actor first"
    # 选取首位最符合的演员名添加到标题末尾
    no_split_title_list = _add_actor_to_title(no_split_title_list, best_match_actor, seq_flag)
    search_title_list = _add_actor_to_title(titles_no_actor, best_match_actor, seq_flag)
    if  seq_flag == "actor first":
        no_split_title = no_split_title_list[0]
    else:
        no_split_title = search_title_list[1]
    return no_split_title, no_split_title_list, search_title_list


def _get_compare_title(pro_pattern, title, actor_list, pattern=None, operation_flags=0b111):
    """
    1. 正则删除标题中的pattern
    2. 调用convert_half处理标题
    3. 删除Amazon标题中的制作商
    4. 删除标题末尾的演员名(支持连续多个演员名), 保留标题中间的演员名
    5. 返回删除演员名前后的标题, 如果标题本身没有演员名, 则返回的两个标题相同
    """
    # 使用正则表达式删除指定的pattern
    if pattern:
        title = re.sub(pattern, "", title).strip()
    
    # 去除括号中的内容, 一般是商品附加信息 常见于开头是 【メーカー特典あり】 或【Amazon.co.jp限定】的标题
    title = re.sub("[(（][^)）]*[)）]", "", title).strip()
    
    # 调用convert_half处理标题
    compare_title_list = convert_half(title, operation_flags)
    
    # 删除Amazon标题中的制作商
    if pro_pattern:
        for i in range(len(compare_title_list)):
            compare_title_list[i] = re.sub(pro_pattern, "", compare_title_list[i]).strip()
    
    # 删除标题末尾的演员名
    compare_title_no_actor = _remove_actor(compare_title_list[0], actor_list)
    
    return compare_title_list[0], compare_title_no_actor

def _get_search_url(search_title):
    """
    说明:
    Amazon 页面包含成人内容时会有年龄验证的弹窗, 如果不手动点击, 则不会显示对应图片, 自然也爬取不到.
    年龄验证页面（通常称为 "Black Curtain" 页面）, 用于限制用户访问某些成人内容或敏感商品, 要求用户完成额外的验证步骤
    eg. https://www.amazon.co.jp/black-curtain/black-curtain?ie=UTF8&returnUrl=%2Fdp%2FB0CQ1FBM88
    
    解决方案:
    1. 将 url 前缀改成 https://www.amazon.co.jp/black-curtain/save-eligibility/black-curtain?returnUrl=/s?k=
        save-eligibility 表示绕过年龄限制, 直接显示成人内容, 出处: https://since2021-10-03.blogspot.com/2021/12/amazon-prime.html
    2. 标题中的空格转为 "+", "?", "&" 等半角符号需要转为全角 (对半角符号暂不做改动)
    2. 对标题进行 urllib.parse.quote_plus 编码
    3. &i=dvd 表示搜索 DVD 类目, &ref=nb_sb_noss 表示自然搜索而非超级链接或推荐机制生成
    """
    url_search = (
        "https://www.amazon.co.jp/black-curtain/save-eligibility/black-curtain?returnUrl=/s?k="
        + urllib.parse.quote_plus(search_title.replace(" ", "+") + "&i=dvd" + "&ref=nb_sb_noss")
    )
    return url_search

def _check_title_matching(
                        compare_title,
                        amazon_compare_title,
                        no_split_compare_title,
                        length_diff_ratio=5,
                        min_match_length=4,
                        mid_title_length=12,
                        no_split_match_ratio=0.5,
                        split_match_ratio=0.8,
                        long_title_length=60,
                        length_ratio=0.4,
                        golden_ratio=0.618,
                        ):
    """
    功能:
        检测标题是否匹配
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
            a. len_amazon/len_compare <= length_diff_ratio, 避免过短未拆分标题匹配到过长Amazon标题 (JUX-925)
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
                    这是专门针对超长标题且搜索结果类同的系列影片 (HUNTA-145)
            d. 再将Amazon标题与拆分标题匹配匹配, 匹配位置必须是拆分标题首字符
            e. 拆分标题长度<= match.ceil(1.5 * min_match_length), 要求短标题完全匹配长标题
            f. 拆分标题长度> match.ceil(1.5 * min_match_length), 要求匹配长度 >= min(math.ceil(len(拆分标题长度) * split_match_ratio), len(短标题))
    返回:
        满足以上匹配条件返回 True, 否则返回 False
    """
    print(f"\n开始匹配标题...")
    len_compare = len(compare_title)
    len_amazon = len(amazon_compare_title)
    len_no_split = len(no_split_compare_title)
    # 获取短标题和长标题
    short_title, long_title = (compare_title, amazon_compare_title) if len_compare < len_amazon else (amazon_compare_title, compare_title)
    len_short = len(short_title)
    len_long = len(long_title)
    # 匹配未拆分标题
    if compare_title == no_split_compare_title:
        print(f"标题未拆分, 遵循既定规则匹配\n未拆分标题: {compare_title}\nAmazon标题: {amazon_compare_title}")
        # 长字符串长度不能超过短字符串长度的5倍
        # if len_long > length_diff_ratio * len_short:
        #     print(f"标题长度差异过大, 匹配失败!")
        #     return False
        
        # 改为 Amazon标题长度不能超过未拆分标题长度的 {length_diff_ratio} 倍
        if len_amazon > len_compare * length_diff_ratio:
            print(f"Amazon标题过长, 未拆分标题过短, 匹配失败!")
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
        print(f"标题已拆分, 遵循既定规则匹配\n已拆分标题: {compare_title}\n未拆分标题: {no_split_compare_title}\nAmazon标题: {amazon_compare_title}")
        print(f"先与未拆分标题匹配")
        if len_amazon <= min_match_length:
            if amazon_compare_title in no_split_compare_title:
                print(f"Amazon标题完全匹配未拆分标题, 继续匹配")
                pass
            else:
                print(f"Amazon标题不完全匹配未拆分标题, 且Amazon标题长度 <={min_match_length}, 匹配失败!")
                return False
        else:
            substring = amazon_compare_title[:min_match_length]  # 从 amazon_compare_title 的首字符开始截取
            if substring in no_split_compare_title:  # 判断子串是否出现在 no_split_compare_title 中
                print(f"Amazon标题与未拆分标题匹配长度 >= {min_match_length}, 继续匹配")
                pass
            else:
                print(f"Amazon标题与未拆分标题匹配长度 < {min_match_length}, 匹配失败!")
                return False
            # 超长标题匹配
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

def _check_title_actor(amazon_compare_title,detail_url, actor_list, pro_pattern, pattern):
    """
    检查详情页链接中的演员是否匹配
    """
    print(f"\n开始匹配标题中的演员...")
    if not actor_list:
        print(f"没有可匹配的演员, 跳过")
        return "NO ACTOR"
    detail_title = re.findall(r".*/(.*)/dp/",detail_url)[0]
    print(f"详情链接中的标题: {detail_title}")
    detail_compare_title, _ = _get_compare_title(pro_pattern, detail_title, actor_list, pattern=pattern)
    print(f"待匹配的详情链标题: {detail_compare_title}")
    print(f"待匹配的Amazon标题: {amazon_compare_title}")
    for actor in actor_list:
        if actor in detail_compare_title:
            print(f"标题匹配到演员: {actor}")
            return "ACTOR MATCH"
    # if (
    #     len(amazon_compare_title) <= len(detail_compare_title) and # 如果 len(amazon_compare_title) > len(detail_compare_title) 说明详情链标题被截断, 跳过匹配
    #     detail_compare_title[-4:] != amazon_compare_title[-4:]):
    #     print(f"标题演员不匹配")
    #     return "ACTOR MISMATCH"
    # 详情链标题格式不统一, 不再判断演员不匹配的情况
    else:
        print(f"未找到演员或含有其他演员, 跳过")
        return "ACTOR UNCERTAIN"

def _get_detail_page_actor(res_li, max_name_length=12):
    detail_actor_list = []
    if res_li:
        # 获取详情页可能的演员信息
        raw_detail_actor_list = []
        for res in res_li:
            if res.strip():  # 检查字符串是否非空（去除前后空白字符后）
                split_elements = re.split(r"[/,]", res)  # 按 "/" 分隔字符串
                cleaned_elements = [re.sub(r"[\W_]+", "", ele) for ele in split_elements] # 删除非字母数字字符
                # 筛选长度 <= max_name_length 的元素，作为人名, 并添加到列表中
                raw_detail_actor_list.extend([ele for ele in cleaned_elements if ele and len(ele) <= max_name_length])
        detail_actor_list = _split_actor(raw_detail_actor_list) if raw_detail_actor_list else []
    return detail_actor_list

def _check_detail_actor(detail_actor_list, actor_list):
    """检查详情页演员是否匹配"""
    if detail_actor_list and actor_list:
        for d in detail_actor_list:
            for actor in actor_list:
                if actor in d["name"]:
                    print(f"详情页匹配到演员: {actor}")
                    return "ACTOR MATCH"
            if d["flag"] == "certain":
                print(f"详情页演员不匹配!")
                return "ACTOR MISMATCH"
            else:
                print(f"购买信息中未匹配到演员, 跳过")
                return "ACTOR UNCERTAIN"
    else:
        print(f"详情页或刮削数据缺少演员信息, 跳过")
        return "NO ACTOR"

def _check_detail_release(json_data, amazon_title, promotion_keywords=[], amazon_release=None):
    """
    比较影片发行日期与Amazon详情页的发行日期是否一致, 避免同一个演员的相同标题影片被误匹配
    1. 如果有任何一个日期不存在, 则返回True
    2. 如果两个日期都存在, 则开始比较
    3. 如果二者间隔 <=30天返回True, 否则返回False, 这是因为有时候影片发行日期取的是配信日期, 会有一定的差异
    4. 如果二者间隔 >30天, 但Amazon影片标题中包含促销推广关键字, 则不认为日期不一致
    """
    print(f"开始检测发行日期是否匹配")
    movie_release = json_data.get("release")
    
    if not amazon_release:
        print(f"Amazon详情页无发行日期, 跳过此检测")
        return "NO RELEASE DATE"
    elif movie_release == "0000-00-00":
        print(f"刮削数据无影片发行日期, 跳过此检测")
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
        print(f"发行日期匹配通过!")
        return "RELEASE MATCH"
    elif any(promotion in amazon_title for promotion in promotion_keywords):
        print(f"发行日期差异过大, 但Amazon影片标题中包含促销推广关键字, 跳过此检测")
        return "PROMOTION"
    print(f"发行日期差异过大!")
    return "RELEASE MISMATCH"

def _check_detail_page(json_data, title_match_ele, actor_list):
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
    print(f"详情页url: {url_new}")
    result, html_detail = get_amazon_data(url_new)
    if result and html_detail:
        html = etree.fromstring(html_detail, etree.HTMLParser())
        # 获取演员名
        # 标题下方的演员名
        detail_actor1 = html.xpath('//span[@class="author notFaded" and .//span[contains(text(), "(出演)")]]//a[@class="a-link-normal"]/text()')
        # 购买信息中的演员名, 一般是software download, 即流媒体类型的影片会包含, 由于没有关键词定位, 只能根据长度大概确认, 如果匹配不到不认为失败
        detail_actor2 = html.xpath('//ul[@class="a-unordered-list a-vertical a-spacing-mini"]/li/span/text()')
        # 登录情报中的演员名, 会比标题下方的演员更全
        detail_actor3 = html.xpath('//span[contains(text(), "出演")]/following-sibling::span[1]/text()')
        detail_actor_list1 = _get_detail_page_actor(detail_actor1)
        detail_actor_list2 = _get_detail_page_actor(detail_actor2)
        detail_actor_list3 = _get_detail_page_actor(detail_actor3)
        exact_acotr_list = detail_actor_list1 + detail_actor_list3
        exact_acotr_list = list(dict.fromkeys(exact_acotr_list))
        detail_actor_list = []
        certain_acotr_dict = {"name": "".join(exact_acotr_list), "flag": "certain"}
        uncertain_acotr_dict = {"name": "".join(detail_actor_list2), "flag": "uncertain"}
        if uncertain_acotr_dict["name"]:
            detail_actor_list.append(uncertain_acotr_dict)
        if certain_acotr_dict["name"]:
            detail_actor_list.append(certain_acotr_dict)
        print(f"开始匹配演员\n详情页包含演员的列表: {detail_actor_list}\n刮削演员列表: {actor_list}")
        check_detail_actor = _check_detail_actor(detail_actor_list, actor_list)

        # 获取发行日期, 即DVD类型影片的発売日, 对于software download, 即流媒体类型, 只有影片的流媒体版本在Amazon的上架日期, 因此不予采纳
        date_text = html.xpath("//span[contains(text(), '発売日')]/following-sibling::span[1]/text()")
        amazon_release = date_text[0].strip() if date_text else ""
        check_detail_release = _check_detail_release(json_data, amazon_title, promotion_keywords, amazon_release)
        
        if (
            check_detail_actor == "ACTOR MISMATCH" or
            check_detail_release == "RELEASE MISMATCH"
            ):
            print(f"详情页匹配失败!")
            return "MATCH FAILED"
        
        if (
            check_detail_release in ["NO RELEASE DATE", "PROMOTION"] and
            check_detail_actor in ["NO ACTOR", "ACTOR UNCERTAIN"]
            ):
            return "LACK PROOF"
        elif check_detail_actor == "ACTOR MATCH":
            return check_detail_actor
        elif check_detail_release == "RELEASE MATCH":
            return check_detail_release
        
    print(f"详情页获取失败, 跳过")
    return "GET DETAIL FAILED"

def get_big_pic_by_amazon(json_data, original_title, raw_actor_list):
    start_time = time.time()
    hd_pic_url = ""
    if not original_title:
        return hd_pic_url
    print(f"\n/--------------------------------Amazon搜图开始--------------------------------/")
    
    # 获取演员列表
    actor_list, best_match_actor = _get_actor_list(json_data, original_title, raw_actor_list)

    # 移除标题中匹配的pattern
    pattern = r"^\[.*?\]|^【.*?】|DVD|オンラインコード版|（DOD）|（BOD）"
    
    # 拆分标题
    print(f"\n开始生成搜索标题列表...")
    params = {
        "original_title": original_title,
        "actor_list": actor_list,
        "best_match_actor": best_match_actor,
        "min_length": 3,
        "pattern": pattern,
        "separator": r"\s+",
        "extra_separator": [r"！+", r"…+", r"。+", r"～+"]
    }
    no_split_title, no_split_title_list, search_title_list = _split_title(**params)
    print(f"\n==== 未拆分的标题列表 共 {len(no_split_title_list)} 个条目 ====")
    print("\n".join(map(str, no_split_title_list)))
    print(f"\n==== 搜索标题总列表 共 {len(search_title_list)} 个条目 ====")
    print("\n".join(map(str, search_title_list)))
    
    # 获取影片制作商和发行商
    print(f"\n获取制作商和发行商...")
    amazon_producer = []
    amazon_studio = json_data.get("amazon_studio")
    if amazon_studio:
        amazon_studio_list = _split_actor([amazon_studio])
        amazon_studio_list = [re.sub(r"[\W_]+", "", studio).upper() for studio in amazon_studio_list]
        print(f"影片制作商: {amazon_studio_list}")
        amazon_producer.extend(amazon_studio_list)
    amazon_publisher = json_data.get("amazon_publisher")
    if amazon_publisher:
        amazon_publisher_list = _split_actor([amazon_publisher])
        amazon_publisher_list = [re.sub(r"[\W_]+", "", publisher).upper() for publisher in amazon_publisher_list]
        print(f"影片发行商: {amazon_publisher_list}")
        amazon_producer.extend(amazon_publisher_list)
    amazon_producer = list(dict.fromkeys(amazon_producer))
    print(f"amazon_producer = {amazon_producer}")
    if amazon_producer:
        pro_pattern = "|".join(re.escape(p) for p in amazon_producer if p.strip())
        print(f"pro_pattern = {pro_pattern}")
    
    # 将未拆分的标题进行处理
    print(f"\n生成待匹配的未拆分标题...")
    no_split_compare_title, no_split_compare_title_no_actor  = _get_compare_title(pro_pattern, no_split_title, actor_list, pattern=pattern)
    print(f"待匹配的未拆分的标题(若末尾存在演员名则保留):\nno_split_compare_title = {no_split_compare_title}")
    print(f"去除末尾演员名后:\nno_split_compare_title_no_actor = {no_split_compare_title_no_actor}")
    
    # 图片url过滤集合, 如果匹配直接跳过
    pic_url_filtered_set = set()
    # 标题过滤集合, 如果匹配直接跳过
    # 不再过滤标题, 对于结果众多的系列影片, 如果没有演员后缀, 标题可能完全相同, 如果标题被过滤, 则无法找到正确影片 (JUL-754)
    # amazon_title_filtered_set = set()
    # 保留列表, 针对标题匹配通过, 但详情页未找到有效匹配数据的图片url
    pic_legacy_list = []
    
    # 搜索标题
    for search_title in search_title_list:
        print(f"\n/********************开始搜索************************/")
        print(f"搜索标题: {search_title}")
        print(f"图片url过滤集合:\npic_url_filtered_set = {pic_url_filtered_set}") 
        
        # 获取搜索 url
        url_search = _get_search_url(search_title)
        print(f"url_search = {url_search}")
        result, html_search = get_amazon_data(url_search)

        if result and html_search:
            html = etree.fromstring(html_search, etree.HTMLParser())
            check_count = 0
            result_summary = html.xpath('//h2[@class="a-size-base a-spacing-small a-spacing-top-small a-text-normal"]/span/text()')
            if not result_summary:
                # 搜索未拆分标题时, 无搜索结果的情况下增加对提示结果的检测 (MVSD-450)
                prompt_list = html.xpath('//h1[@class="a-size-medium a-color-base a-text-normal"]/span/text()')
                prompt_message = prompt_list[0] if prompt_list else ""
                print(f"prompt_message = {prompt_message}")
                if (
                    (prompt_message == "キーワードを絞るか、以下をお試しください。") and
                    (search_title in no_split_title_list)
                    ):
                    print(f"未拆分标题搜索无结果, 但存在提示结果, 加入检测列表")
                    amazon_result = html.xpath('//div[@data-index="2"]//div[@class="a-section a-spacing-base"]')
                    check_count = len(amazon_result)
                else:
                    print(f"/*************无搜索结果, 结束本次搜索****************/\n")
                    continue
            else:
                result_count = int(re.findall(r"\d+", result_summary[0])[0])
                check_count = result_count
                print(f"找到 {result_count} 个搜索结果")
                # 增加对推荐结果的检测
                recommend_result = html.xpath('//div[@class="a-section a-spacing-base a-spacing-top-base"]/span/text()')
                if "All Departments" in recommend_result:
                    recommend_summary = html.xpath('//div[@class="a-section a-spacing-base a-spacing-top-base"]/a/text()')
                    recommend_count = int(re.findall(r"\d+", recommend_summary[0])[0])
                    print(f"找到 {recommend_count} 个推荐结果")
                    if recommend_count <= 4:
                        print(f"将推荐结果加入检测列表")
                        check_count += recommend_count
                    else:
                        print(f"推荐结果数量过多, 跳过")
                amazon_result = html.xpath('//div[@class="a-section a-spacing-base"]')
                    
            # 标题匹配成功的列表
            title_match_list = []
            # 计算无效标题的数量, 避免在无效关键词上浪费过多时间
            invalid_result_count = 0
            amazon_result = html.xpath('//div[@class="a-section a-spacing-base"]')
            
            # 开始处理搜索结果
            print(f"将检测 {check_count} 个结果")
            for each_result in amazon_result[:check_count]:
                if invalid_result_count == 6:
                    print(f"/**********无效结果数量过多, 结束本次搜索*************/\n")
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
                    # 去除非 (DVD|流媒体)与无图片的结果
                    if (amazon_category not in ["DVD", "Software Download"]
                        or ".jpg" not in pic_trunc_url
                    ):
                        print(f"\n无效结果, 跳过\n标题: {amazon_title}\n图片url: {pic_trunc_url}")
                        invalid_result_count += 1
                        print(f"添加到过滤集合, 继续检测其他搜索结果")
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    
                    if pic_trunc_url in pic_url_filtered_set:
                        invalid_result_count += 1
                        print(f"\n跳过已过滤的结果\n标题: {amazon_title}\n图片url: {pic_trunc_url}")
                        continue
                    
                    w, h = get_imgsize(pic_trunc_url)
                    if w < 700 or w >= h:
                        print(f"\n图片非高清或非竖版, 跳过\n标题: {amazon_title}\n图片url: {pic_trunc_url}")
                        invalid_result_count += 1
                        print(f"添加到过滤集合, 继续检测其他搜索结果")
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    
                    # 避免单体作品取到合集结果 GVH-435
                    collection_keywords = ['BEST', '時間', '総集編', '完全', '枚組']
                    skip_flag = False
                    for collection_keyword in collection_keywords:
                        is_collection1 = collection_keyword in str(no_split_title_list[0]).upper()
                        is_collection2 = collection_keyword in str(amazon_title).upper()
                        if (
                            (not is_collection1) # 刮削影片非合集
                            and is_collection2   # 搜索结果为合集
                            ):
                            skip_flag = True
                    if skip_flag:
                        print(f"\n合集标题, 跳过\n标题: {amazon_title}\n图片url: {pic_trunc_url}")
                        # 合集不再统计无效结果数 VENX-002
                        # invalid_result_count += 1
                        print(f"添加到过滤集合, 继续检测其他搜索结果")
                        pic_url_filtered_set.add(pic_trunc_url)
                        continue
                    print(f"\n/++++++++++++++++++检测有效结果+++++++++++++++++++++/")
                    print(f"搜索标题: {search_title}")
                    print(f"Amazon影片信息:\n影片类别: {amazon_category}\n影片标题: {amazon_title}\npic_trunc_url = {pic_trunc_url}")
                    if pic_trunc_url in pic_url_filtered_set:
                        print(f"\n跳过已过滤的图片url: {pic_trunc_url}")
                        continue
                    
                    # 获取待匹配的标题
                    compare_title, compare_title_no_actor = _get_compare_title(pro_pattern, search_title, actor_list, pattern=pattern)
                    amazon_compare_title, amazon_compare_title_no_actor = _get_compare_title(pro_pattern, amazon_title, actor_list, pattern=pattern)
                    
                    # 判断标题是否匹配
                    # 只匹配去除末尾演员名的标题, 演员名单独匹配
                    print(f"待匹配的搜索标题: {compare_title_no_actor}")
                    print(f"待匹配的Amazon标题: {amazon_compare_title_no_actor}")
                    print(f"待匹配的未拆分标题(末尾不含演员名): {no_split_compare_title_no_actor}")
                    is_match = _check_title_matching(compare_title_no_actor, amazon_compare_title_no_actor, no_split_compare_title_no_actor)
                    
                    if is_match:
                        print(f"标题匹配成功!")
                        detail_url = urllib.parse.unquote_plus(detail_url)
                        detail_url_full = "https://www.amazon.co.jp" + detail_url
                        
                        # 判断演员是否匹配
                        check_title_actor = _check_title_actor(amazon_compare_title,detail_url, actor_list, pro_pattern, pattern)
                        if check_title_actor == "ACTOR MATCH":
                            print(f"匹配成功, 采用此结果!\n标题: {amazon_title}\n图片url: {pic_trunc_url}")
                            hd_pic_url = pic_trunc_url
                            print(f"/--------------------------------Amazon搜图结束--------------------------------/")
                            end_time = time.time()
                            execution_time = end_time - start_time
                            print(f"Elapsed time: {execution_time:.2f}s\n")
                            return hd_pic_url
                        elif check_title_actor == "ACTOR MISMATCH": # 不再判断演员不匹配
                            print(f"匹配失败, 跳过\n标题: {amazon_title}\n图片url: {pic_trunc_url}")
                            # 演员匹配失败不再统计无效结果数, 对于系列影片, 有时添加演员名搜索无结果, 无演员名搜索时, 如果排序靠后, 可能会提前结束搜索 VENX-002
                            # invalid_result_count += 1
                            print(f"添加到过滤集合, 继续检测其他搜索结果")
                            pic_url_filtered_set.add(pic_trunc_url)
                        else:
                            print(f"添加到 title_match_list\n标题: {amazon_title}\n图片url: {pic_trunc_url}")
                            title_match_list.append([pic_trunc_url, detail_url_full, amazon_title])
                            print(f"title_match_list_pic_only = {[element[0] for element in title_match_list]}")
                    else:
                        print(f"标题不匹配, 跳过\n标题: {amazon_title}\n图片url: {pic_trunc_url}")
                        invalid_result_count += 1
                        print(f"添加到过滤集合, 继续检测其他搜索结果")
                        pic_url_filtered_set.add(pic_trunc_url)
                else:
                    print(f"\n跳过不包含类型, 图片, 标题, 详情页面的结果")
                    invalid_result_count += 1
                    pass
                    
            # 当搜索结果匹配到标题，没有匹配到演员时，尝试去详情页获取演员信息
            if (len(title_match_list) > 0):
                print(f"\n尝试去详情页获取演员信息, 尝试最多4个结果")
                for each in title_match_list[:4]:
                    detail_page_match =  _check_detail_page(json_data, each, actor_list)
                    if detail_page_match in ["ACTOR MATCH", "RELEASE MATCH"]:
                        print(f"详情页检测通过, 采用此结果!")
                        print(f"/--------------------------------Amazon搜图结束--------------------------------/")
                        end_time = time.time()
                        execution_time = end_time - start_time
                        print(f"Elapsed time: {execution_time:.2f}s\n")
                        return each[0]
                    elif detail_page_match == "LACK PROOF":
                        print(f"详情页未找到有效信息, 将图片url添加到过滤列表和保留列表")
                        pic_url_filtered_set.add(each[0])
                        pic_legacy_list.append(each[0])
                    else:
                        print(f"详情页检测未通过, 将图片url添加到过滤集合, 继续检测其他搜索结果")
                        pic_url_filtered_set.add(each[0])

    # 从保留列表选取可能结果
    if pic_legacy_list:
        pic_legacy_list = list(dict.fromkeys(pic_legacy_list))
        print(f"已经尝试所有搜索, 仍未找到确切匹配结果, 选取可能的结果")
        print(f"图片保留列表\npic_legacy_list = {pic_legacy_list}")
        print(f"选取结果\npic_legacy_list[0] = {pic_legacy_list[0]}")
        print(f"/--------------------------------Amazon搜图结束--------------------------------/\n")
        end_time = time.time()
        execution_time = end_time - start_time
        print(f"Elapsed time: {execution_time:.2f}s\n")
        return pic_legacy_list[0]
    return hd_pic_url
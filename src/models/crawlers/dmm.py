#!/usr/bin/python
import json
import re
import time  # yapf: disable # NOQA: E402

import urllib3
from lxml import etree

from models.base.web import check_url, get_html, get_url_playwright

urllib3.disable_warnings()  # yapf: disable


# import traceback


def get_title(html):
    result = html.xpath('//h1[@id="title"]/text()')
    if not result:
        result = html.xpath('//h1[@class="item fn bold"]/text()')
    return result[0].strip() if result else ""


def get_actor(html):
    result = html.xpath("//span[@id='performer']/a/text()")
    if not result:
        result = html.xpath("//td[@id='fn-visibleActor']/div/a/text()")
    if not result:
        result = html.xpath("//td[contains(text(),'出演者')]/following-sibling::td/a/text()")
    return ",".join(result)


def get_actor_photo(actor):
    actor = actor.split(",")
    data = {}
    for i in actor:
        actor_photo = {i: ""}
        data.update(actor_photo)
    return data


def get_mosaic(html):
    result = html.xpath('//li[@class="on"]/a/text()')
    return "里番" if result and result[0] == "アニメ" else "有码"


def get_studio(html):
    result = html.xpath("//td[contains(text(),'メーカー')]/following-sibling::td/a/text()")
    return result[0] if result else ""


def get_publisher(html, studio):
    result = html.xpath("//td[contains(text(),'レーベル')]/following-sibling::td/a/text()")
    return result[0] if result else studio


def get_runtime(html):
    result = html.xpath("//td[contains(text(),'収録時間')]/following-sibling::td/text()")
    if not result or not re.search(r"\d+", str(result[0])):
        result = html.xpath("//th[contains(text(),'収録時間')]/following-sibling::td/text()")
    if result and re.search(r"\d+", str(result[0])):
        return re.search(r"\d+", str(result[0])).group()
    return ""


def get_series(html):
    result = html.xpath("//td[contains(text(),'シリーズ')]/following-sibling::td/a/text()")
    if not result:
        result = html.xpath("//th[contains(text(),'シリーズ')]/following-sibling::td/a/text()")
    return result[0] if result else ""


def get_year(release):
    return re.search(r"\d{4}", str(release)).group() if release else ""


def get_release(html):
    result = html.xpath("//td[contains(text(),'発売日')]/following-sibling::td/text()")
    if not result:
        result = html.xpath("//th[contains(text(),'発売日')]/following-sibling::td/text()")
    if not result:
        result = html.xpath("//td[contains(text(),'配信開始日')]/following-sibling::td/text()")
    if not result:
        result = html.xpath("//th[contains(text(),'配信開始日')]/following-sibling::td/text()")

    release = result[0].strip().replace("/", "-") if result else ""
    result = re.findall(r"(\d{4}-\d{1,2}-\d{1,2})", release)
    return result[0] if result else ""


def get_tag(html):
    result = html.xpath("//td[contains(text(),'ジャンル')]/following-sibling::td/a/text()")
    if not result:
        result = html.xpath(
            "//div[@class='info__item']/table/tbody/tr/th[contains(text(),'ジャンル')]/following-sibling::td/a/text()"
        )
    return str(result).strip(" ['']").replace("', '", ",")


def get_cover(html, detail_url):
    if "mono/dvd" in detail_url:
        result = html.xpath('//meta[@property="og:image"]/@content')
        if result:
           return result[0]
    elif "dmm.co.jp" in detail_url:
        result = html.xpath('//a[@id="sample-image1"]/img/@src')
        if result:
            # 替换域名并返回第一个匹配项
            return re.sub(r'pics.dmm.co.jp', r'awsimgsrc.dmm.co.jp/pics_dig', result[0])
    return ''  # 无匹配时返回空字符串


def get_poster(html, cover, detail_url):
    result = html.xpath('//meta[@property="og:image"]/@content')
    if result and "dmm.co.jp/digital" in detail_url:
        result = re.sub(r"pics.dmm.co.jp", r"awsimgsrc.dmm.co.jp/pics_dig", result[0])
        return result
    else:
        return cover.replace("pl.jpg", "ps.jpg")


def get_extrafanart(html, detail_url):
    result = []
    if "mono/dvd" in detail_url:
        result_list = html.xpath("//a[@name='sample-image']/img/@data-lazy")
        i = 1
        for each in result_list:
            each = each.replace("-%s.jpg" % i, "jp-%s.jpg" % i)
            result.append(each)
            i += 1
    elif "dmm.co.jp" in detail_url:
        result_list = html.xpath("//div[@id='sample-image-block']/a/img/@src")
        if not result_list:
            result_list = html.xpath("//a[@name='sample-image']/img/@src")
        i = 0
        for each in result_list:
            each = each.replace("-%s.jpg" % i, "jp-%s.jpg" % i)
            result.append(each)
            i += 1
    return result


def get_director(html):
    result = html.xpath("//td[contains(text(),'監督')]/following-sibling::td/a/text()")
    if not result:
        result = html.xpath("//th[contains(text(),'監督')]/following-sibling::td/a/text()")
    return result[0] if result else ""

def remove_content(input_string):
    # 定义关键词列表，支持普通字符串和正则表达式模式
    keywords = [
        r"---+",
        "【※", 
        "【エスワン20周年",
        "『制作·著作",
        "＃班長P",
        "初回無料体験ポ",
        "＃班長P",
        r"★.*★",
        "※ 配信",
        "※こちらは",
        "特集"
    ]
    
    # 遍历关键词列表，按优先级逐一匹配
    for keyword in keywords:
        # 判断是否是正则表达式模式
        if isinstance(keyword, str) and (keyword.startswith(r"---+") or keyword.startswith(r"★.*★")):
            # 如果是正则表达式模式，直接编译
            pattern = re.compile(keyword)
        else:
            # 如果是普通字符串，使用 re.escape 转义后编译
            pattern = re.compile(re.escape(keyword))
        
        # 查找匹配
        match = pattern.search(input_string)
        if match:
            # print(f"Found match: {keyword}")
            # 如果找到匹配，截取到匹配点之前的部分并返回
            return input_string[:match.start()].strip()
    
    # 如果没有找到任何关键词，则返回原始字符串
    return input_string.strip()


def get_outline(html, detail_url):
    result = ""
    if "mono/dvd" in detail_url:
        result = html.xpath("normalize-space(string(//div[@class='mg-b20 lh4']/p[@class='mg-b20']))")
        result = remove_content(result)
    elif "dmm.co.jp" in detail_url:
        result = html.xpath(
            "normalize-space(string(//div[@class='wp-smplex']/preceding-sibling::div[contains(@class, 'mg-b20')][1]))"
        )
        result = remove_content(result)
    return result


def get_score(html):
    result = html.xpath("//p[contains(@class,'d-review__average')]/strong/text()")
    return result[0].replace("\\n", "").replace("\n", "").replace("点", "") if result else ""


def get_trailer(htmlcode, detail_url):
    trailer_url = ""
    normal_cid = re.findall(r'cid=(.*?)/', detail_url)[0]
    vr_cid = re.findall(r"https://www.dmm.co.jp/digital/-/vr-sample-player/=/cid=([^/]+)", htmlcode)
    if vr_cid:
        cid = vr_cid[0]
        temp_url = "https://cc3001.dmm.co.jp/vrsample/{0}/{1}/{2}/{2}vrlite.mp4".format(cid[:1], cid[:3], cid)
        trailer_url = check_url(temp_url)
    elif normal_cid:
        cid = normal_cid
        if "dmm.co.jp" in detail_url:
            url = (
                "https://www.dmm.co.jp/service/digitalapi/-/html5_player/=/cid=%s/mtype=AhRVShI_/service=digital/floor=videoa/mode=/"
                % cid
            )
        else:
            url = (
                "https://www.dmm.com/service/digitalapi/-/html5_player/=/cid=%s/mtype=AhRVShI_/service=digital/floor=videoa/mode=/"
                % cid
            )

        result, htmlcode = get_html(url)
        try:
            var_params = re.findall(r" = ({[^;]+)", htmlcode)[0].replace(r"\/", "/")
            trailer_url = json.loads(var_params).get("src")
            if trailer_url.startswith("//"):
                trailer_url = "https:" + trailer_url
        except:
            trailer_url = ""
    return trailer_url

def get_detail_url(url_list, number, number2, file_path):
    number_temp = number2.lower().replace("-", "")
    # https://tv.dmm.co.jp/list/?content=mide00726&i3_ref=search&i3_ord=1
    # https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=mide00726/?i3_ref=search&i3_ord=2
    # https://www.dmm.com/mono/dvd/-/detail/=/cid=n_709mmrak089sp/?i3_ref=search&i3_ord=1
    # /cid=snis00900/
    # /cid=snis126/ /cid=snis900/ 图上面没有蓝光水印
    # /cid=h_346rebdb00017/
    # /cid=6snis027/ /cid=7snis900/
    number1 = number_temp.replace("000", "")
    number_pre = re.compile(f"(?<=[=0-9]){number_temp[:3]}")
    number_end = re.compile(f"{number_temp[-3:]}(?=(-[0-9])|([a-z]*)?[/&])")
    number_mid = re.compile(f"[^a-z]{number1}[^0-9]")
    temp_list = []
    for each in url_list:
        if (number_pre.search(each) and number_end.search(each)) or number_mid.search(each):
            cid_list = re.findall(r"(cid|content)=([^/&]+)", each)
            if cid_list:
                temp_list.append(each)
                cid = cid_list[0][1]
                if "-" in cid:  # 134cwx001-1
                    if cid[-2:] in file_path:
                        number = cid

    # 网址排序：digital(数据完整)  >  dvd(无前缀数字，图片完整)   >   prime（有发行日期）   >   premium（无发行日期）  >  s1（无发行日期）
    tv_list = []
    digital_list = []
    dvd_list = []
    prime_list = []
    monthly_list = []
    other_list = []
    for i in temp_list:
        if "tv.dmm.co.jp" in i:
            tv_list.append(i)
        elif "/digital/" in i:
            digital_list.append(i)
        elif "/dvd/" in i:
            dvd_list.append(i)
        elif "/prime/" in i:
            prime_list.append(i)
        elif "/monthly/" in i:
            monthly_list.append(i)
        else:
            other_list.append(i)
    dvd_list.sort(reverse=True)
    # 丢弃 tv_list, 因为获取其信息调用的后续 api 无法访问
    detail_url_list = digital_list + dvd_list + prime_list + monthly_list + other_list
    return detail_url_list, number

def main(number, specified_url="", log_info="", req_web="", language="jp", file_path=""):
    start_time = time.time()
    website_name = "dmm"
    req_web += "-> %s" % website_name
    cookies = {"cookie": "uid=abcd786561031111; age_check_done=1;"}
    css_selector = "div[class='flex py-1.5 pl-3'] > a"
    # price_selector = "span.font-bold.text-lg"
    title = ""
    cover_url = ""
    poster_url = ""
    mosaic = "有码"
    release = ""
    year = ""
    image_download = False
    image_cut = "right"
    dic = {}
    digits = ""
    if x := re.findall(r"[A-Za-z]+-?(\d+)", number):
        digits = x[0]
        if len(digits) >= 5:
            if digits.startswith("00"):
                number = number.replace(digits, digits[2:])
    number_00 = number.lower().replace(digits, digits.zfill(5)).replace("-", "")  # 数字不足5位则在起始位补0, 搜索结果多，但snis-027没结果
    number_no_00 = number.lower().replace("-", "")  # 搜索结果少
    web_info = "\n       "
    log_info += " \n    🌐 dmm"
    debug_info = ""

    if not specified_url:
        search_url = "https://www.dmm.co.jp/search/=/searchstr=%s/sort=ranking/" % number_00  # 带00
        debug_info = "搜索地址: %s " % search_url
        log_info += web_info + debug_info
    else:
        debug_info = "番号地址: %s " % specified_url
        log_info += web_info + debug_info

    try:
        if "tv.dmm." not in search_url:
            page_url, url_list = get_url_playwright(search_url, cookies=cookies, css_selector=css_selector)
            # print(f"page_url: {page_url}, url_list: {url_list}")
            if not page_url:  # 请求失败
                debug_info = "网络请求错误: %s " % search_url
                log_info += web_info + debug_info
                raise Exception(debug_info)

            if re.findall("age_check", page_url):
                debug_info = "年龄限制, 请确认cookie 有效！"
                log_info += web_info + debug_info
                raise Exception(debug_info)
            
            if re.findall("not-available-in-your-region", page_url):  # 非日本地区限制访问
                debug_info = "地域限制, 请使用日本节点访问！"
                log_info += web_info + debug_info
                raise Exception(debug_info)

            # html = etree.fromstring(htmlcode, etree.HTMLParser())

            # 未指定详情页地址时，获取详情页地址（刚才请求的是搜索页）
            if not specified_url:
                detail_url_list, number = get_detail_url(url_list, number, number, file_path)
                if not detail_url_list:
                    debug_info = "搜索结果: 未匹配到番号！"
                    log_info += web_info + debug_info
                    if number_no_00 != number_00:
                        search_url = (
                            "https://www.dmm.co.jp/search/=/searchstr=%s/sort=ranking/" % number_no_00
                        )  # 不带00，旧作 snis-027
                        debug_info = "再次搜索地址: %s " % search_url
                        log_info += web_info + debug_info
                        page_url, url_list = get_url_playwright(search_url, cookies=cookies, css_selector=css_selector)
                        if not page_url:  # 请求失败
                            debug_info = "网络请求错误: %s " % search_url
                            log_info += web_info + debug_info
                            raise Exception(debug_info)
                        # html = etree.fromstring(htmlcode, etree.HTMLParser())
                        detail_url_list, number = get_detail_url(url_list, number, number_no_00, file_path)
                        if not detail_url_list:
                            debug_info = "搜索结果: 未匹配到番号！"
                            log_info += web_info + debug_info

                if not detail_url_list:
                    # 写真
                    search_url = "https://www.dmm.com/search/=/searchstr=%s/sort=ranking/" % number_no_00
                    debug_info = "再次搜索地址: %s " % search_url
                    log_info += web_info + debug_info
                    page_url, url_list = get_url_playwright(search_url, cookies=cookies, css_selector=css_selector)
                    if not page_url:  # 请求失败
                        debug_info = "网络请求错误: %s " % search_url
                        log_info += web_info + debug_info
                        raise Exception(debug_info)
                    # html = etree.fromstring(htmlcode, etree.HTMLParser())
                    detail_url_list, number0 = get_detail_url(url_list, number, number_no_00, file_path)
                    if not detail_url_list:
                        debug_info = "搜索结果: 未匹配到番号！"
                        log_info += web_info + debug_info

                else:
                    detail_url_list = [re.sub(r"\?.*", "", detail_url) for detail_url in detail_url_list]

        # 获取详情页信息
        for detail_url in detail_url_list:
            try:
                # 获取 HTML 内容
                result, htmlcode = get_html(detail_url, cookies=cookies)
                html = etree.fromstring(htmlcode, etree.HTMLParser())
                # 检查网络请求是否成功
                if not result:
                    debug_info = "网络请求错误: %s " % htmlcode
                    log_info += web_info + debug_info
                    raise Exception(debug_info)
                # 检查页面是否为 404
                if "404 Not Found" in str(
                    html.xpath("//span[@class='d-txten']/text()")
                ):  # 如果页面有 404，表示传入的页面地址不对
                    debug_info = "404! 页面地址错误！"
                    log_info += web_info + debug_info
                    raise Exception(debug_info)
                # 获取标题并检查是否为空
                title = get_title(html).strip()  # 获取标题
                if not title:
                    debug_info = "数据获取失败: 未获取到 title！"
                    log_info += web_info + debug_info
                    raise Exception(debug_info)
                # 尝试解析详细信息
                try:
                    actor = get_actor(html)  # 获取演员
                    cover_url = get_cover(html, detail_url)  # 获取 cover
                    outline = get_outline(html, detail_url)
                    tag = get_tag(html)
                    release = get_release(html)
                    year = get_year(release)
                    runtime = get_runtime(html)
                    score = get_score(html)
                    series = get_series(html)
                    director = get_director(html)
                    studio = get_studio(html)
                    publisher = get_publisher(html, studio)
                    extrafanart = get_extrafanart(html, detail_url)
                    poster_url = get_poster(html, cover_url, detail_url)
                    trailer = get_trailer(htmlcode, detail_url)
                    mosaic = get_mosaic(html)
                    # 如果所有解析成功，结束循环
                    debug_info = "番号地址: %s " % detail_url
                    log_info += web_info + debug_info
                    break
                except Exception as e:
                    # 捕获异常并记录日志
                    debug_info = "出错: %s" % str(e)
                    log_info += web_info + debug_info
                    raise Exception(debug_info)
            except Exception as e:
                # 如果发生异常，打印日志并继续下一个 URL
                debug_info = "出错: %s" % str(e)
                log_info += web_info + debug_info
                continue
                
        # 如果循环结束后仍未找到有效数据
        if not title:
            debug_info = "未找到有效数据: %s" % str(e)
            log_info += web_info + debug_info
            raise Exception(debug_info)
        actor_photo = get_actor_photo(actor)
        if "VR" in title:
            image_download = True
        try:
            dic = {
                "number": number,
                "title": title,
                "originaltitle": title,
                "actor": actor,
                "outline": outline,
                "originalplot": outline,
                "tag": tag,
                "release": release,
                "year": year,
                "runtime": runtime,
                "score": score,
                "series": series,
                "director": director,
                "studio": studio,
                "publisher": publisher,
                "source": "dmm",
                "website": detail_url,
                "actor_photo": actor_photo,
                "cover": cover_url,
                "poster": poster_url,
                "extrafanart": extrafanart,
                "trailer": trailer,
                "image_download": image_download,
                "image_cut": image_cut,
                "log_info": log_info,
                "error_info": "",
                "req_web": req_web
                + "(%ss) "
                % (
                    round(
                        (time.time() - start_time),
                    )
                ),
                "mosaic": mosaic,
                "wanted": "",
            }
            debug_info = "数据获取成功！"
            log_info += web_info + debug_info
            dic["log_info"] = log_info
        except Exception as e:
            debug_info = "数据生成出错: %s" % str(e)
            log_info += web_info + debug_info
            raise Exception(debug_info)

    except Exception as e:
        # print(traceback.format_exc())
        debug_info = str(e)
        dic = {
            "title": "",
            "cover": "",
            "website": "",
            "log_info": log_info,
            "error_info": debug_info,
            "req_web": req_web
            + "(%ss) "
            % (
                round(
                    (time.time() - start_time),
                )
            ),
        }
    dic = {website_name: {"zh_cn": dic, "zh_tw": dic, "jp": dic}}
    js = json.dumps(dic, ensure_ascii=False, sort_keys=False, indent=4, separators=(",", ": "))  # .encode('UTF-8')
    return js


if __name__ == "__main__":
    # yapf: disable
    # print(main('ipz-825'))    # 普通，有预告片
    # print(main('SIVR-160'))     # vr，有预告片
    # print(main('enfd-5301'))  # 写真，有预告片
    # print(main('h_346rebdb00017'))  # 无预告片
    # print(main('', 'https://www.dmm.com/mono/dvd/-/detail/=/cid=n_641enfd5301/'))
    # print(main('', 'https://www.dmm.co.jp/rental/ppr/-/detail/=/cid=4ssis243/?i3_ref=search&i3_ord=1'))
    # print(main('NKD-229'))
    # print(main('rebdb-017'))         # 测试搜索，无视频
    # print(main('STARS-199'))    # poster图片
    # print(main('ssis301'))  # 普通预告片
    # print(main('hnvr00015'))
    # print(main('QNBM-094'))
    # print(main('ssis-243'))
    # print(main('1459525'))
    # print(main('ssni888'))    # detail-sample-movie 1个
    # print(main('snis-027'))
    # print(main('gs00002'))
    # print(main('SMBD-05'))
    # print(main('cwx-001', file_path='134cwx001-1.mp4'))
    # print(main('ssis-222'))
    # print(main('snis-036'))
    # print(main('GLOD-148'))
    # print(main('（抱き枕カバー付き）自宅警備員 1stミッション イイナリ巨乳長女・さやか～編'))    # 番号最后有字母
    # print(main('エロコンビニ店長 泣きべそ蓮っ葉・栞〜お仕置きじぇらしぃナマ逸機〜'))
    # print(main('初めてのヒトヅマ 第4話 ビッチな女子の恋愛相談'))
    # print(main('ACMDP-1035'))
    # print(main('JUL-066'))
    # print(main('mide-726'))
    # print(main('1dandy520'))
    # print(main('ome-210'))
    # print(main('ftbd-042'))
    # print(main('mmrak-089'))
    # print(main('', 'https://tv.dmm.co.jp/list/?content=juny00018'))
    # print(main('snis-900'))
    # print(main('n1581'))
    # print(main('ssni-888'))
    # print(main('ssni00888'))
    # print(main('ssni-288'))
    # print(main('', 'https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=ssni00288/'))
    # print(main('俺をイジメてた地元ヤンキーの巨乳彼女を寝とって復讐を果たす話 The Motion Anime'))  # 模糊匹配 MAXVR-008
    # print(main('', 'https://www.dmm.co.jp/mono/dvd/-/detail/=/cid=h_173dhry23/'))   # 地域限制
    # print(main('ssni00288'))
    # print(main('ssni00999'))
    # print(main('ipx-292'))
    # print(main('wicp-002')) # 无视频
    # print(main('ssis-080'))
    # print(main('DV-1562'))
    # print(main('mide00139', "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=mide00139"))
    # print(main('mide00139', ""))
    # print(main('kawd00969'))
    # print(main('', 'https://tv.dmm.com/vod/detail/?title=5533ftbd00042&season=5533ftbd00042'))
    # print(main('stars-779'))
    # print(main('FAKWM-001', 'https://tv.dmm.com/vod/detail/?season=5497fakwm00001'))
    # print(main('FAKWM-064', 'https://tv.dmm.com/vod/detail/?season=5497fakwm00064'))
    # print(main('IPZ-791'))
    # print(main('FPRE-113'))
    # print(main('fpre00113'))
    # print(main('FPRE113'))
    # print(main('ABF-164'))
    # print(main('ABF-203'))
    # print(main('IPZZ-300'))
    # print(main('HODV-21938'))
    # print(main('HAVD-459'))
    # print(main('PRBY-089'))
    pass

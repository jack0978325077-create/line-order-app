import streamlit as st
import pandas as pd
from google import genai
import json
import io
from PIL import Image
from datetime import datetime
import calendar
import time
import re
from supabase import create_client, Client

# =========================================================
# ⚙️ 系統基本設定 (強制置頂)
# =========================================================
st.set_page_config(
    page_title="LINE 熟客叫貨智慧系統",
    page_icon="📦",
    layout="wide",                # 👈 Windows / Mac 寬螢幕會自動撐滿
    initial_sidebar_state="auto"  # 👈 手機版開啟時側邊欄會自動收合
)

# 📱 注入全平台 CSS 自適應微調
st.markdown("""
    <style>
    .stButton>button {
        border-radius: 8px;
        padding: 0.5rem 1rem;
        font-weight: 600;
    }
    html, body, [data-testid="stAppViewContainer"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans CJK TC", sans-serif;
    }
    /* 修正元件上方過大的空白 */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    </style>
""", unsafe_allow_html=True)

# =================【雲端資料庫連線設定區】=================
SUPABASE_URL = "https://ktmepyfafstgxklrwhoq.supabase.co" 
SUPABASE_KEY = "sb_publishable_ROyxuswMSHsq0uymo9UVyw_K1qM3MYO"

@st.cache_resource
def init_supabase():
    """使用快取優化連線，避免重覆初始化造成資源浪費與畫面閃爍"""
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"❌ 雲端資料庫連線初始化嚴重失敗: {str(e)}")
        return None

supabase = init_supabase()
# =========================================================

# =========================================================
# 🔄 全域 Session State 初始化 (防止冷啟動時 Key 遺失)
# =========================================================
INIT_KEYS = {
    "final_c_name": "",
    "final_c_id": "",
    "items_a_cached": [],
    "items_b_cached": [],
    "trigger_recalc": False,
    "is_ai_mode": False,
    "unified_text_val": "",
    "img_run_counter": 0,
    "date_ignored_cache": False,
    "selected_date_cache": datetime.now().strftime("%Y/%m/%d"),
    "ai_detected_group_name": ""
}
for k, v in INIT_KEYS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# 後台導航側邊欄
st.sidebar.markdown("## 🗂️ 系統功能導航後台")
db_mode = st.sidebar.radio(
    "請選擇操作功能：", 
    [
        "Line圖片文字叫貨",
        "🚚 配送排單行事曆",
        "📦 全品項商品主檔",
        "🏢 管理客戶主檔" ,
        "🏪 全廠揀貨理貨大總管"
    ]
)

# =========================================================
# 🛠️ 共用智慧工具函式
# =========================================================
def clean_string(name_str):
    if not name_str: return ""
    return str(name_str).strip().replace(" ", "").lower()

def advanced_clean_product_name(name_str):
    """無形剪刀：自動蒸發所有括號、小表情、空格與加號"""
    if not name_str: return ""
    s = str(name_str).strip().replace(" ", "").lower()
    s = re.sub(r'[【】\[\]\(\)（）📣\+\~\=\*✨`「」\'\"]', '', s)
    s = re.sub(r'\d+$', '', s) # 剃除尾部可能的數字干擾
    return s

def clean_line_noise(raw_text):
    if not raw_text: return ""
    cleaned_lines = []
    for line in raw_text.split("\n"):
        l_str = line.strip()
        if not l_str: continue
        if "@all" in l_str.lower() or "登記" in l_str or "出貨品項" in l_str or "開團" in l_str: 
            continue
        cleaned_lines.append(l_str)
    return "\n".join(cleaned_lines)

# =========================================================
# 1. Line圖片文字叫貨
# =========================================================
if db_mode == "Line圖片文字叫貨":  
    st.title("LINE 熟客叫貨智慧扣帳自動導航系統 🚀")
    
    parsed_date = datetime.now().strftime("%Y/%m/%d")
    
    # AI 提示詞設定
    PROMPT_CLEAN_A = (
        "Return JSON format only: {'items': [{'raw_item_name': '純品名', 'quantity': 1}]}. "
        "STRICT RULE for quantities: Often lines end with a trailing number representing the ordering quantity "
        "(e.g., '黃金鴨掌20' -> quantity is 20; '三色黎麥+10' -> quantity is 10). "
        "You must extract this trailing number as the 'quantity', and strip it from the 'raw_item_name'. "
        "EXCEPTION: If the number is part of a specification unit at the very end (like '1000g', '2kg'), "
        "that is part of the product name, NOT the order quantity."
    )
    
    PROMPT_CLEAN_B = PROMPT_CLEAN_A # 邏輯基準保持一致

    st.subheader("🏢 本次對帳客戶資訊確認")
    
    # 安全拉取客戶主檔 (不再 pass 錯誤，確保看得到問題)
    all_customers = []
    if supabase:
        try:
            cust_db = supabase.table("customers").select("*").execute()
            all_customers = cust_db.data if cust_db.data else []
        except Exception as ce:
            st.error(f"⚠️ 無法取得雲端客戶資料: {str(ce)}")

    # 建立下拉對帳選單清單
    search_options = ["請選擇或輸入文字搜尋客戶... (留空代表查詢當日全廠)"]
    cust_mapping = {}
    for c in all_customers:
        c_name = c.get("standard_name", "未命名客戶")
        c_id = c.get("customer_id", "無ID")
        kw = c.get("search_keywords", "")
        shortcut_str = ""
        
        if "SHORTCUT:" in kw:
            shortcut_str = kw.split(" , ", 1)[0].replace("SHORTCUT:", "").strip()
        
        shortcuts = [s.strip() for s in shortcut_str.replace("，", ",").split(",") if s.strip()]
        display_shortcut = " / ".join(shortcuts) if shortcuts else "無縮寫"
        display_label = f"【{display_shortcut}】{c_name} ({c_id})"
        search_options.append(display_label)
        cust_mapping[display_label] = {"name": c_name, "id": c_id, "raw_data": c}

    # 檢查是否偵測到未綁定的群組特徵
    ai_detected_group = st.session_state.get("ai_detected_group_name", "").strip()
    is_ai_unrecognized = False
    
    current_index = 0
    if st.session_state["final_c_name"]:
        for idx, opt in enumerate(search_options):
            if idx > 0 and cust_mapping[opt]["name"] == st.session_state["final_c_name"]:
                current_index = idx
                break

    if ai_detected_group and current_index == 0:
        is_ai_unrecognized = True

    # 智慧記憶學習區塊
    if is_ai_unrecognized:
        st.warning(f"🤖 **AI 智慧學習偵測**：偵測到群組特徵『**{ai_detected_group}**』，但雲端尚未建立綁定。")
        with st.expander("🔗 點此一鍵綁定客戶", expanded=True):
            learn_select = st.selectbox("🎯 請指定此特徵歸屬於哪位正式客戶：", options=search_options, key="learn_cust_box")
            if learn_select != "請選擇或輸入文字搜尋客戶... (留空代表查詢當日全廠)":
                target_cust = cust_mapping[learn_select]["raw_data"]
                if st.button("💾 確認綁定，讓系統學會此特徵", use_container_width=True):
                    old_kw = target_cust.get("search_keywords", "")
                    new_kw = f"{old_kw}, {ai_detected_group}" if old_kw else ai_detected_group
                    try:
                        supabase.table("customers").update({"search_keywords": new_kw}).eq("customer_id", target_cust["customer_id"]).execute()
                        st.session_state["final_c_name"] = target_cust["standard_name"]
                        st.session_state["final_c_id"] = target_cust["customer_id"]
                        st.session_state["ai_detected_group_name"] = "" # 清空特徵
                        st.success(f"🎉 學習成功！系統已將『{ai_detected_group}』與客戶【{target_cust['standard_name']}】完美連結！")
                        time.sleep(1)
                        st.rerun()
                    except Exception as le: 
                        st.error(f"寫入記憶失敗: {str(le)}")

    # 計算狀態 Key 宣告
    has_customer = st.session_state["final_c_name"] != ""
    unique_calc_id = f"pool_{st.session_state['final_c_id']}" if has_customer else "pool_all_factory"
    state_key = f"df_pool_{unique_calc_id}"
    excel_ready_key = f"excel_pool_ready_{unique_calc_id}"

    # 智慧多功能控制面板
    c_edit_1, c_edit_2, c_edit_3 = st.columns([4, 2, 2])
    with c_edit_1:
        selected_box = st.selectbox("👤 輸入自訂快搜 / 全名 / 編號即時過濾：", options=search_options, index=current_index)
        if selected_box != "請選擇或輸入文字搜尋客戶... (留空代表查詢當日全廠)":
            if st.session_state["final_c_name"] != cust_mapping[selected_box]["name"]:
                st.session_state["final_c_name"] = cust_mapping[selected_box]["name"]
                st.session_state["final_c_id"] = cust_mapping[selected_box]["id"]
                st.rerun()
        else:
            if st.session_state["final_c_name"] != "":
                st.session_state["final_c_name"] = ""
                st.session_state["final_c_id"] = ""
                st.rerun()

        enable_all_dates = st.checkbox("🔄 跨日累計查詢 (忽略日期，累計蓄水池所有未出貨品項)", value=st.session_state["date_ignored_cache"])
        if enable_all_dates != st.session_state["date_ignored_cache"]:
            st.session_state["date_ignored_cache"] = enable_all_dates
            st.rerun()

    with c_edit_2: 
        try: 
            init_date = datetime.strptime(st.session_state["selected_date_cache"].replace("/", "-"), "%Y-%m-%d")
        except: 
            init_date = datetime.today()
        chosen_date = st.date_input("📅 單據對帳日期：", value=init_date, disabled=enable_all_dates)
        final_date = chosen_date.strftime("%Y/%m/%d")
        if final_date != st.session_state["selected_date_cache"]:
            st.session_state["selected_date_cache"] = final_date

    with c_edit_3:
        st.markdown("<div style='padding-top:24px;'></div>", unsafe_allow_html=True)
        btn_query_only = st.button("🔍 查詢目前登記品項", key="btn_just_query", use_container_width=True)

    # 模式情境小提示
    if not has_customer:
        st.info(f"📋 **目前模式**：全廠當日總覽看板 (日期: {final_date})")
    elif enable_all_dates:
        st.success(f"🟢 **目前模式**：【{st.session_state['final_c_name']}】— 跨日歷史累積總量")
    else:
        st.success(f"🟢 **目前模式**：【{st.session_state['final_c_name']}】— 標準單日對帳 (日期: {final_date})")

    st.markdown("---")
    input_mode = st.sidebar.radio("對帳輸入模式：", ["📸 圖片截圖上傳模式", "✍️ 純文字複製貼上模式"], index=1, horizontal=True)
    api_key = st.sidebar.text_input("Gemini API Key", value="AQ.Ab8RN6K7Kir0lqgMowA52Bo5tLY23cn7_lQ9dJhAvHPm913iSA", type="password")

# 💡 新增：AI 認不到人時的「智慧引導綁定彈窗」 (完全靠左，前方不留空格)
@st.dialog("🤖 AI 未能識別客戶：請協助綁定特徵", width="large")
def dialog_bind_unknown_customer(detected_group, search_options, cust_mapping):
    # 🎯 修正重點：def 下方的所有程式碼，開頭都必須多 4 個空格！
    st.markdown(f"系統從圖片中偵測到群組名稱為：`{detected_group}`，但雲端目前無此綁定。")

    action_type = st.radio("請選擇處理方式：", ["🔗 綁定到現有客戶", "✨ 建立全新客戶並綁定"], horizontal=True)
    st.markdown("---")

    if action_type == "🔗 綁定到現有客戶":
        selected_opt = st.selectbox("🎯 請指定歸屬於哪位正式客戶：", options=search_options, key="dialog_link_cust")
        if selected_opt != "請選擇或輸入文字搜尋客戶... (留空代表查詢當日全廠)":
            target_cust = cust_mapping[selected_opt]["raw_data"]
            if st.button("💾 確認綁定舊客", use_container_width=True):
                old_kw = target_cust.get("search_keywords", "")
                new_kw = f"{old_kw}, {detected_group}" if old_kw else detected_group
                try:
                    supabase.table("customers").update({"search_keywords": new_kw}).eq("customer_id", target_cust["customer_id"]).execute()
                    # 立即灌回當前 Session 狀態，讓後續功能按鈕亮起
                    st.session_state["final_c_name"] = target_cust["standard_name"]
                    st.session_state["final_c_id"] = target_cust["customer_id"]
                    st.session_state["ai_detected_group_name"] = ""
                    st.success(f"🎉 成功！已將『{detected_group}』永久綁定至【{target_cust['standard_name']}】")
                    time.sleep(1)
                    st.rerun()
                except Exception as le: 
                    st.error(f"寫入失敗: {str(le)}")
                
    else:
        new_c_id = st.text_input("🔢 新客戶編號 (例如: XV270099)").strip()
        new_c_name = st.text_input("🏢 新客戶官方標準全名").strip()
        if st.button("💾 創立新客並直接綁定", use_container_width=True):
            if new_c_id and new_c_name:
                try:
                    # 直接寫入客戶主檔，特徵直接代入
                    combined_keywords = f"SHORTCUT: , {detected_group}"
                    supabase.table("customers").insert({
                        "customer_id": new_c_id,
                        "standard_name": new_c_name,
                        "search_keywords": combined_keywords
                    }).execute()
                    # 立即灌回當前 Session 狀態
                    st.session_state["final_c_name"] = new_c_name
                    st.session_state["final_c_id"] = new_c_id
                    st.session_state["ai_detected_group_name"] = ""
                    st.success(f"🎉 成功！已自動創立【{new_c_name}】並完成圖片特徵綁定！")
                    time.sleep(1)
                    st.rerun()
                except Exception as le: 
                    st.error(f"建立新客失敗: {str(le)}")
            else:
                st.error("❌ 請務必填寫完整的編號與名稱！")

# 這裡的縮排前方一律維持 4 個空格，對齊 if input_mode
    uploaded_file = st.file_uploader("📤 請上傳電腦版 LINE 視窗截圖 (支援 PNG, JPG, JPEG)", type=["png", "jpg", "jpeg"])
    
    if uploaded_file and api_key:
        if st.button("⚡ 開始執行圖片 AI 智慧拆解並帶入暫存區", key="btn_img_go", use_container_width=True):
            try:
                client = genai.Client(api_key=api_key)
                bytes_data = uploaded_file.getvalue()
                pil_image = Image.open(io.BytesIO(bytes_data))

                with st.spinner("⏳ 正在利用最新視覺模型解構您的 LINE 截圖..."):
                    res_img = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=[pil_image, PROMPT_IMAGE_BRAIN]
                    )
                    
                    # 🎯 修正重點：確保 re.sub 的參數緊緊連在一起，不可以斷行！
                    raw_text = res_img.text.strip()
                    clean_json = re.sub(r"^```json\s*|```$", "", raw_text, flags=re.MULTILINE).strip()
                    data_img = json.loads(clean_json, strict=False)
                    
                    st.session_state["unified_text_val"] = data_img.get("formatted_text", "")
                    
                    detected_group = clean_string(data_img.get("line_group_name", ""))
                    found_match = False
                    
                    if detected_group:
                        for c in all_customers:
                            c_kw = clean_string(c.get("search_keywords", ""))
                            if detected_group in c_kw or c_kw in detected_group or clean_string(c.get("standard_name")) in detected_group:
                                st.session_state["final_c_name"] = c["standard_name"]
                                st.session_state["final_c_id"] = c["customer_id"]
                                st.session_state["ai_detected_group_name"] = ""
                                found_match = True
                                break
                        
                        if not found_match:
                            st.session_state["ai_detected_group_name"] = data_img.get("line_group_name", "").strip()
                            st.session_state["img_run_counter"] += 1
                            dialog_bind_unknown_customer(st.session_state["ai_detected_group_name"], search_options, cust_mapping)
                            st.stop()

                    if found_match:
                        st.success(f"🎉 圖片解構完成！已自動對齊熟客：【{st.session_state['final_c_name']}】")
                    
                    st.session_state["img_run_counter"] += 1
                    time.sleep(0.5)
                    st.rerun()
            except Exception as img_err:
                st.error(f"❌ 圖片視覺解析失敗，原因：{str(img_err)}")

    st.markdown("---")
    
    dynamic_box_key = f"txt_area_unified_v_{st.session_state['img_run_counter']}"
    unified_text = st.text_area(
        "📋 圖片/文字叫貨拆解暫存區 (可自由增刪修改內容)", 
        value=st.session_state["unified_text_val"], 
        height=200, 
        key=dynamic_box_key
    )

    if unified_text.strip() and not has_customer:
        st.warning("⚠️ **【請注意】** AI 已抓出文字，但「尚未選定對帳客戶」！請手動在最上方下拉選單指定客戶。")

    if unified_text and api_key:
        pure_text = clean_line_noise(unified_text)
        btn_col1, btn_col2 = st.columns(2)
        
        with btn_col1:
            if st.button("📦 這些品項均『登記需出貨品項』(存入雲端)", key="btn_all_ship_go", use_container_width=True, disabled=not has_customer):
                try:
                    client = genai.Client(api_key=api_key)
                    with st.spinner("⏳ 正在將今日品項合併儲存至雲端..."):
                        res_a = client.models.generate_content(model='gemini-2.5-flash', contents=[pure_text, PROMPT_CLEAN_A])
                        clean_res_a = re.sub(r"^```json\s*|```$", "", res_a.text.strip(), flags=re.MULTILINE).strip()
                        items_a = json.loads(clean_res_a, strict=False).get("items", [])
                        
                        prod_master_db = supabase.table("product_master").select("*").execute()
                        master_products = prod_master_db.data if prod_master_db.data else []
                        
                        for item_a in items_a:
                            raw_name_a = str(item_a.get("raw_item_name", "")).strip()
                            ultra_clean_a = advanced_clean_product_name(raw_name_a)
                            qty_a = int(pd.to_numeric(item_a.get("quantity", 1), errors='coerce') or 1)
                            
                            std_prod_name = raw_name_a
                            for p in master_products:
                                db_p_name = p.get("product_name", "")
                                if advanced_clean_product_name(db_p_name) in ultra_clean_a or ultra_clean_a in advanced_clean_product_name(db_p_name):
                                    std_prod_name = db_p_name
                                    break
                            
                            check_exist = supabase.table("delivery_orders").select("*").eq("customer_name", st.session_state["final_c_name"]).eq("product_name", std_prod_name).eq("status", "已登記未出貨").eq("delivery_date", final_date).execute()
                            if check_exist.data:
                                supabase.table("delivery_orders").update({"quantity": int(check_exist.data[0]["quantity"]) + qty_a}).eq("id", check_exist.data[0]["id"]).execute()
                            else:
                                supabase.table("delivery_orders").insert({"delivery_date": final_date, "customer_name": st.session_state["final_c_name"], "product_name": std_prod_name, "quantity": qty_a, "status": "已登記未出貨"}).execute()

                        st.session_state["unified_text_val"] = ""
                        st.session_state["trigger_recalc"] = True
                        st.success("🎉 明細已順利存入雲端蓄水池！")
                        time.sleep(0.5)
                        st.rerun()
                except Exception as err: st.error(f"❌ 登記失敗：{str(err)}")
                    
        with btn_col2:
            if st.button("❌ 這些品項『目前無出貨』，其餘品項均出貨", key="btn_remain_b_go", use_container_width=True, disabled=not has_customer):
                try:
                    client = genai.Client(api_key=api_key)
                    with st.spinner("⏳ 自動核銷並結轉欠貨軌跡中..."):
                        pool_res = supabase.table("delivery_orders").select("*").eq("customer_name", st.session_state["final_c_name"]).eq("status", "已登記未出貨").execute()
                        pool_list = pool_res.data if pool_res.data else []
                        
                        pool_dict = {}
                        for x in pool_list:
                            p_name = x["product_name"]
                            pool_dict[p_name] = pool_dict.get(p_name, 0) + int(x["quantity"])

                        res_b = client.models.generate_content(model='gemini-2.5-flash', contents=[pure_text, PROMPT_CLEAN_B])
                        clean_res_b = re.sub(r"^```json\s*|```$", "", res_b.text.strip(), flags=re.MULTILINE).strip()
                        items_b = json.loads(clean_res_b, strict=False).get("items", [])
                        
                        b_cleaned_dict = {}
                        for b_item in items_b:
                            b_n = str(b_item.get("raw_item_name", "")).strip()
                            b_q = int(pd.to_numeric(b_item.get("quantity", 0), errors='coerce') or 0)
                            if b_n and b_q > 0: b_cleaned_dict[advanced_clean_product_name(b_n)] = b_q

                        for prod_name in pool_dict.keys():
                            supabase.table("delivery_orders").delete().eq("customer_name", st.session_state["final_c_name"]).eq("product_name", prod_name).eq("status", "已登記未出貨").execute()

                        pm_db = supabase.table("product_master").select("product_id", "product_name", "price").execute().data
                        p_dict = {p["product_name"]: p for p in pm_db} if pm_db else {}

                        excel_rows = []
                        for prod_name, total_pool_qty in pool_dict.items():
                            clean_pool_name = advanced_clean_product_name(prod_name)
                            matched_b_qty = 0
                            for b_k, b_v in b_cleaned_dict.items():
                                if b_k in clean_pool_name or clean_pool_name in b_k:
                                    matched_b_qty = b_v
                                    break
                            
                            actual_ship = max(0, total_pool_qty - matched_b_qty)
                            
                            if actual_ship > 0:
                                supabase.table("delivery_orders").insert({"delivery_date": final_date, "customer_name": st.session_state["final_c_name"], "product_name": prod_name, "quantity": actual_ship, "status": "已出貨"}).execute()
                                pr_info = p_dict.get(prod_name, {})
                                excel_rows.append({
                                    "單據類型": "出貨", "訂單編號": "LINE一鍵智慧核銷", "客戶編號": str(st.session_state["final_c_id"]),
                                    "客戶名稱": str(st.session_state["final_c_name"]), "日期": str(final_date),
                                    "商品編號": str(pr_info.get("product_id", "新編號")), "商品名稱": str(prod_name),
                                    "數量": actual_ship, "單價": float(pr_info.get("price", 0)), "總金額": float(pr_info.get("price", 0)) * actual_ship
                                })

                            if matched_b_qty > 0:
                                supabase.table("delivery_orders").insert({"delivery_date": final_date, "customer_name": st.session_state["final_c_name"], "product_name": prod_name, "quantity": matched_b_qty, "status": "已登記未出貨"}).execute()

                        if excel_rows:
                            output = io.BytesIO()
                            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                                pd.DataFrame(excel_rows).to_excel(writer, index=False, sheet_name='本次核銷出貨明細')
                            st.session_state[excel_ready_key] = output.getvalue()

                        st.session_state["unified_text_val"] = ""
                        st.session_state["trigger_recalc"] = True
                        st.success("🎉 核銷處理完成！已重新生成定格帳目軌跡！")
                        time.sleep(0.5)
                        st.rerun()
                except Exception as err: 
                    # 🎯 修正重點：把這個 except 補回來，try-except 結構就完整了！
                    st.error(f"❌ 核銷失敗：{str(err)}")
    # --- ✍️ 純文字複製貼上模式 ---
    else:
        txt_col_a, txt_col_b = st.columns(2)
        with txt_col_a: text_a = st.text_area("📋 客戶叫貨/加單 純文字", height=150, key="txt_area_a", disabled=not has_customer)
        with txt_col_b: text_b = st.text_area("📋 未發貨/餘剩 純文字", height=150, key="txt_area_b", disabled=not has_customer)
        
        if (text_a or text_b) and api_key and has_customer:
            if st.button("⚡ 開始執行純文字智慧拆解分析", key="btn_txt_go", use_container_width=True):
                # 💡 移除造成空白死鎖的 Thread 多執行緒邏輯，改由 Streamlit 安全的循序處理
                try:
                    client = genai.Client(api_key=api_key)
                    pure_text_a = clean_line_noise(text_a)
                    pure_text_b = clean_line_noise(text_b)
                    
                    res_items_a = []
                    res_items_b = []
                    
                    with st.spinner("⏳ 正在極速核銷分析中..."):
                        if pure_text_a:
                            PROMPT_WITH_GROUP = PROMPT_CLEAN_A + " Also detect line group name if any into form: {'line_group_name': '群組名', 'items': [...]}"
                            res_a = client.models.generate_content(model='gemini-2.5-flash', contents=[pure_text_a, PROMPT_WITH_GROUP])
                            data_a = json.loads(re.sub(r"^```json\s*|```$", "", res_a.text.strip(), flags=re.MULTILINE).strip(), strict=False)
                            res_items_a = data_a.get("items", [])
                            
                            detected_group = clean_string(data_a.get("line_group_name", ""))
                            if detected_group:
                                st.session_state["ai_detected_group_name"] = detected_group
                        
                        if pure_text_b:
                            res_b = client.models.generate_content(model='gemini-2.5-flash', contents=[pure_text_b, PROMPT_CLEAN_B])
                            data_b = json.loads(re.sub(r"^```json\s*|```$", "", res_b.text.strip(), flags=re.MULTILINE).strip(), strict=False)
                            res_items_b = data_b.get("items", [])

                    st.session_state["items_a_cached"] = res_items_a
                    st.session_state["items_b_cached"] = res_items_b
                    st.session_state["trigger_recalc"] = True
                    st.session_state["is_ai_mode"] = True
                    st.rerun()
                except Exception as tx_err:
                    st.error(f"❌ 純文字智慧拆解失敗: {str(tx_err)}")

# ==================== 智慧核心核銷扣帳運算區 ====================
    is_any_ai_data = len(st.session_state["items_a_cached"]) > 0 or len(st.session_state["items_b_cached"]) > 0
    
    if (btn_query_only or st.session_state["trigger_recalc"] or is_any_ai_data or state_key not in st.session_state):
        try:
            prod_master_db = supabase.table("product_master").select("*").execute() if supabase else None
            master_products = prod_master_db.data if (prod_master_db and prod_master_db.data) else []
            
            items_a = st.session_state["items_a_cached"]
            items_b = st.session_state["items_b_cached"]

            if has_customer and st.session_state["trigger_recalc"] and items_a and input_mode == "✍️ 純文字複製貼上模式":
                for item_a in items_a:
                    raw_name_a = str(item_a.get("raw_item_name", "")).strip()
                    ultra_clean_a = advanced_clean_product_name(raw_name_a)
                    qty_a = int(pd.to_numeric(item_a.get("quantity", 1), errors='coerce') or 1)
                    
                    std_prod_name = raw_name_a
                    for p in master_products:
                        db_p_name = p.get("product_name", "")
                        if advanced_clean_product_name(db_p_name) in ultra_clean_a or ultra_clean_a in advanced_clean_product_name(db_p_name):
                            std_prod_name = db_p_name
                            break
                            
                    check_exist = supabase.table("delivery_orders").select("*").eq("customer_name", st.session_state["final_c_name"]).eq("product_name", std_prod_name).eq("status", "已登記未出貨").eq("delivery_date", final_date).execute()
                    if check_exist.data:
                        supabase.table("delivery_orders").update({"quantity": int(check_exist.data[0]["quantity"]) + qty_a}).eq("id", check_exist.data[0]["id"]).execute()
                    else:
                        supabase.table("delivery_orders").insert({"delivery_date": final_date, "customer_name": st.session_state["final_c_name"], "product_name": std_prod_name, "quantity": qty_a, "status": "已登記未出貨"}).execute()

            # 撈取該客欠貨單
            table_rows = []
            pool_list = []
            if supabase:
                query_builder = supabase.table("delivery_orders").select("*").eq("status", "已登記未出貨")
                if has_customer:
                    query_builder = query_builder.eq("customer_name", st.session_state["final_c_name"])
                if not enable_all_dates:
                    query_builder = query_builder.eq("delivery_date", final_date)
                pool_list = query_builder.execute().data or []

            pool_dict = {}
            for x in pool_list:
                p_name = x["product_name"]
                pool_dict[p_name] = pool_dict.get(p_name, 0) + int(x["quantity"])

            b_cleaned_dict = {}
            for b_item in items_b:
                b_name = str(b_item.get("raw_item_name", "")).strip()
                b_qty = int(pd.to_numeric(b_item.get("quantity", 0), errors='coerce') or 0)
                if b_name and b_qty > 0:
                    b_cleaned_dict[advanced_clean_product_name(b_name)] = b_qty

            for prod_name, total_pool_qty in pool_dict.items():
                clean_pool_name = advanced_clean_product_name(prod_name)
                matched_b_qty = 0
                for b_k, b_v in b_cleaned_dict.items():
                    if b_k in clean_pool_name or clean_pool_name in b_k:
                        matched_b_qty = b_v
                        break
                
                if st.session_state["is_ai_mode"] or len(items_b) > 0:
                    actual_ship = max(0, total_pool_qty - matched_b_qty)
                    if matched_b_qty == 0: action_note = "B檔留空➔判定全出"
                    elif actual_ship == 0: action_note = "❌ 現場全無到貨➔今日不出"
                    else: action_note = f"⚠️ 部分到貨(出{actual_ship}/欠{matched_b_qty})"
                else:
                    actual_ship = 0
                    action_note = "歷史蓄水池待出貨" if enable_all_dates else "當日待出貨項目"

                final_unit_price = 0.0
                p_id = "新編號"
                for p in master_products:
                    if p.get("product_name") == prod_name:
                        final_unit_price = float(p.get("price", 0.0))
                        p_id = p.get("product_id", "新編號")
                        break

                table_rows.append({
                    "商品編號": p_id, "商品名稱": prod_name, "雲端累積總量": total_pool_qty,
                    "B檔未發剩餘": matched_b_qty, "實際出貨數量": actual_ship, "單價": final_unit_price,
                    "總金額": actual_ship * final_unit_price, "核銷動作": action_note
                })
            
            st.session_state[state_key] = pd.DataFrame(table_rows)
            st.session_state["items_a_cached"] = []
            st.session_state["items_b_cached"] = []
            st.session_state["trigger_recalc"] = False
            st.session_state["is_ai_mode"] = False 
            st.rerun()
        except Exception as calc_err:
            st.error(f"❌ 核心核銷扣帳運算發生錯誤: {str(calc_err)}")

    # ==================== 📊 獨立偵測滾動核銷面板渲染區 ====================
    st.subheader("📊 獨立偵測滾動核銷面板")
    if state_key in st.session_state and not st.session_state[state_key].empty:
        df_current = st.session_state[state_key]
        is_standard_checkout_ready = has_customer and (not enable_all_dates)

        show_cols = ["商品編號", "商品名稱", "雲端累積總量", "核銷動作"] if not has_customer else ["商品編號", "商品名稱", "雲端累積總量", "B檔未發剩餘", "實際出貨數量", "單價", "總金額", "核銷動作"]
        st.dataframe(df_current, use_container_width=True, column_order=show_cols, hide_index=True)

        if is_standard_checkout_ready:
            c_btn1, c_btn2 = st.columns(2)
            with c_btn1:
                if st.button("📦 貨品已全出 (一鍵滿額)", use_container_width=True):
                    for idx, row in st.session_state[state_key].iterrows():
                        st.session_state[state_key].at[idx, "實際出貨數量"] = row["雲端累積總量"]
                        st.session_state[state_key].at[idx, "B檔未發剩餘"] = 0  
                        st.session_state[state_key].at[idx, "總金額"] = row["雲端累積總量"] * row["單價"]
                        st.session_state[state_key].at[idx, "核銷動作"] = "小編手動判定➔一鍵全出"
                    st.success("⚡ 已強制填滿實際出貨數量！")
                    st.rerun()
            
            with c_btn2:
                if st.button("🔒 核對無誤，確認結帳並輸出 Excel", key="btn_do_checkout", use_container_width=True):
                    with st.spinner("⏳ 正在定格出貨明細與重整欠單..."):
                        df_final = st.session_state[state_key]
                        for _, r in df_final.iterrows():
                            p_name = r["商品名稱"]
                            act_qty = int(r["實際出貨數量"])
                            rem_qty = int(r["B檔未發剩餘"])
                            
                            # 斬草除根舊未出貨單
                            try:
                                supabase.table("delivery_orders").delete().eq("customer_name", st.session_state["final_c_name"]).eq("product_name", p_name).eq("status", "已登記未出貨").execute()
                            except: pass

                            if act_qty > 0:
                                supabase.table("delivery_orders").insert({"delivery_date": final_date, "customer_name": st.session_state["final_c_name"], "product_name": p_name, "quantity": act_qty, "status": "已出貨"}).execute()
                            
                            if rem_qty > 0:
                                supabase.table("delivery_orders").insert({"delivery_date": final_date, "customer_name": st.session_state["final_c_name"], "product_name": p_name, "quantity": rem_qty, "status": "已登記未出貨"}).execute()
                        
                        # 重新生成標準 Excel 明細
                        excel_rows = []
                        for _, r in st.session_state[state_key].iterrows():
                            final_ship_qty = int(r.get("實際出貨數量", 0))
                            if final_ship_qty > 0:
                                excel_rows.append({
                                    "單據類型": "出貨", "訂單編號": "LINE智慧對帳核銷", "客戶編號": str(st.session_state["final_c_id"]),
                                    "客戶名稱": str(st.session_state["final_c_name"]), "日期": str(final_date),
                                    "商品編號": str(r.get("商品編號")), "商品名稱": str(r.get("商品名稱")),
                                    "數量": final_ship_qty, "單價": float(r.get("單價", 0)), "總金額": float(r.get("總金額", 0))
                                })
                        
                        if excel_rows:
                            output = io.BytesIO()
                            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                                pd.DataFrame(excel_rows).to_excel(writer, index=False, sheet_name='本日核銷出貨明細')
                            st.session_state[excel_ready_key] = output.getvalue()
                        
                        st.success("🎉 雲端出貨數據軌跡定格成功！")
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.warning("⚠️ 提示：跨日累計或全廠瀏覽狀態下已自動鎖定結帳功能，以防帳目錯亂。")
    else:
        st.info("💡 當前查詢條件下，雲端蓄水池內無待出貨登記項。")

    # Excel 下載按鈕智慧渲染
    if excel_ready_key in st.session_state and st.session_state[excel_ready_key] is not None and has_customer and (not enable_all_dates):
        st.markdown("---")
        st.download_button(
            label=f"📥 下載【{st.session_state['final_c_name']}】出貨對帳 Excel 報表", 
            data=st.session_state[excel_ready_key], 
            file_name=f"對帳單_{final_date.replace('/', '')}_{st.session_state['final_c_name']}.xlsx", 
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# ====================
# 2. 🚚 配送排單行事曆
# ====================
elif db_mode == "🚚 配送排單行事曆":
    st.title("📆 雲端配送排單行事曆月看板")
    
    @st.dialog("📋 雲端配送單據明細詳情", width="large")
    def show_order_detail(c_name, c_date, target_status):
        st.subheader(f"🏢 {c_name} — 明細")
        if supabase:
            try:
                detail_res = supabase.table("delivery_orders").select("product_name, quantity, status").eq("customer_name", c_name).eq("delivery_date", c_date).eq("status", target_status).execute()
                if detail_res.data: 
                    st.dataframe(pd.DataFrame(detail_res.data), use_container_width=True, hide_index=True)
                else: 
                    st.info("暫無明細項目")
            except Exception as e: 
                st.error(f"明細撈取錯誤: {str(e)}")

    today = datetime.today()
    col_y, col_m = st.columns(2)
    with col_y: select_year = st.selectbox("年份", list(range(today.year - 1, today.year + 3)), index=1)
    with col_m: select_month = st.selectbox("月份", list(range(1, 13)), index=today.month - 1)
    
    start_date_str = f"{select_year}-{select_month:02d}-01"
    _, last_day = calendar.monthrange(select_year, select_month)
    end_date_str = f"{select_year}-{select_month:02d}-{last_day:02d}"
    
    orders_data = []
    if supabase:
        try:
            db_res = supabase.table("delivery_orders").select("delivery_date, customer_name, status").gte("delivery_date", start_date_str).lte("delivery_date", end_date_str).execute()
            orders_data = db_res.data if db_res.data else []
        except Exception as calendar_err:
            st.error(f"行事曆資料載入錯誤: {str(calendar_err)}")

    cal = calendar.Calendar(firstweekday=6)
    month_weeks = cal.monthdayscalendar(select_year, select_month)
    week_days = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"]
    
    header_cols = st.columns(7)
    for idx, day_name in enumerate(week_days):
        bg_color = "#CC0000" if idx == 0 or idx == 6 else "#1E1E1E"
        header_cols[idx].markdown(f"<div style='text-align:center; font-weight:bold; background-color:{bg_color}; color:white; padding:8px; border-radius:5px 5px 0px 0px; font-size:14px;'>{day_name}</div>", unsafe_allow_html=True)
    
    for week in month_weeks:
        day_cols = st.columns(7)
        for idx, day in enumerate(week):
            with day_cols[idx]:
                if day != 0:
                    current_date_str = f"{select_year}-{select_month:02d}-{day:02d}"
                    day_orders = [o for o in orders_data if str(o.get("delivery_date")).replace("/", "-") == current_date_str or str(o.get("delivery_date")) == current_date_str]
                    
                    yellow_labels, green_labels = set(), set()
                    for ord in day_orders:
                        c_name, status = ord.get("customer_name", "未知客戶"), ord.get("status")
                        if status == "已登記未出貨": yellow_labels.add(c_name)
                        elif status in ["自由核銷已出貨", "已出貨"]: green_labels.add(c_name)
                    
                    with st.container(border=True):
                        is_today = (today.year == select_year and today.month == select_month and today.day == day)
                        day_style = "background-color:#FF4B4B; color:white; padding:2px 6px; border-radius:50%;" if is_today else "font-weight:bold; color:#E0E0E0;"
                        st.markdown(f"<div style='text-align:right; margin-bottom:5px;'><span style='{day_style}'>{day}</span></div>", unsafe_allow_html=True)
                        
                        for cust in sorted(yellow_labels):
                            if st.button(f"🟡 {cust}", key=f"y_{current_date_str}_{cust}", use_container_width=True): show_order_detail(cust, current_date_str, "已登記未出貨")
                        for cust in sorted(green_labels):
                            if st.button(f"🟢 {cust}", key=f"g_{current_date_str}_{cust}", use_container_width=True): show_order_detail(cust, current_date_str, "已出貨")
                else:
                    with st.container(border=True): st.markdown("<div style='min-height:40px;'>&nbsp;</div>", unsafe_allow_html=True)
# ==================== 3. 📦 全品項商品主檔 ====================
elif db_mode == "📦 全品項商品主檔":  # 👈 🎯 修正重點：補上「商品」兩個字，與選單完全對齊！
    st.header("📦 全品項商品雲端主檔 (🎯 鎖定 G 欄批價版)")
    st.write("支援樂廚自訂商品 Excel 匯入：自動補滿類別、**放生 H 欄，精準抓取 G 欄（成本/批價）作為系統基準定價**。")
    
    ADMIN_PASSWORD = "123"  

    # 確保連線正常才抓取
    existing_ids = set()
    if supabase:
        try:
            existing_res = supabase.table("product_master").select("product_id").execute()
            existing_ids = {str(row['product_id']).strip() for row in existing_res.data} if existing_res.data else set()
        except Exception as e:
            st.warning(f"無法載入現成品項 ID 清單: {str(e)}")

    with st.expander("📥 批次匯入新版商品 Excel 檔案", expanded=True):
        p_excel_file = st.file_uploader("請選擇要匯入的商品主檔 Excel (.xlsx)", type=["xlsx"])
        if p_excel_file:
            try:
                df_p_import = pd.read_excel(p_excel_file, skiprows=1)
                df_p_import = df_p_import.dropna(how='all')
                df_p_import.iloc[:, 0] = df_p_import.iloc[:, 0].ffill()
                
                valid_rows = df_p_import[df_p_import.iloc[:, 1].notna() & df_p_import.iloc[:, 2].notna()]
                total_read_rows = len(valid_rows)
                st.info(f"📋 系統成功讀取！此 Excel 中包含有編號及品名的有效商品共有 **{total_read_rows}** 筆。")
                st.dataframe(df_p_import.head(5))
                
                total_cols = len(df_p_import.columns)
                if total_cols >= 7:
                    if st.button("🚀 確認將上方全數商品匯入雲端 (鎖定 G 欄批價)"):
                        success_p_count = 0
                        new_product_count = 0

                        def fix_unit_typo(text):
                            if not text or text == "nan": return text
                            return re.sub(r'([包支片盒袋罐碗顆元/\d\s])(?:香|相)', r'\1箱', str(text))

                        for idx, row in df_p_import.iterrows():
                            p_category = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else "一般類"
                            p_id = str(row.iloc[1]).strip()
                            p_name = str(row.iloc[2]).strip()
                            p_unit_d = str(row.iloc[3]).strip()
                            p_spec_e = str(row.iloc[4]).strip()
                            p_temp_f = str(row.iloc[5]).strip()
                            p_cost_g = str(row.iloc[6]).strip()

                            p_unit_d = fix_unit_typo(p_unit_d)
                            p_cost_g = fix_unit_typo(p_cost_g)

                            p_price = 0.0
                            if pd.notna(row.iloc[6]):
                                price_match = re.search(r'\d+', p_cost_g)
                                p_price = float(price_match.group()) if price_match else 0.0

                            cat_info = f"類別:{p_category}"
                            pack_info = f"包裝:{p_unit_d}" if p_unit_d and p_unit_d != "nan" else ""
                            spec_info = f"規格:{p_spec_e}" if p_spec_e and p_spec_e != "nan" else ""
                            temp_info = f"溫層:{p_temp_f}" if p_temp_f and p_temp_f != "nan" else ""
                            cost_info = f"批價:{p_cost_g}" if p_cost_g and p_cost_g != "nan" else ""

                            notes_list = [cat_info, pack_info, spec_info, temp_info, cost_info]
                            final_unit_combined = " | ".join([info for info in notes_list if info.strip()])

                            if p_id and p_name and p_id != "nan" and p_name != "nan" and p_id.lower() != "null":
                                if p_id.endswith(".0"): p_id = p_id[:-2]
                                is_new = p_id not in existing_ids

                                try:
                                    supabase.table("product_master").upsert({
                                        "product_id": p_id,
                                        "product_name": p_name,
                                        "unit": final_unit_combined,
                                        "price": p_price
                                    }).execute()
                                    success_p_count += 1
                                    if is_new: new_product_count += 1
                                except Exception:  # 🎯 修正重點：把 catch 改回 Python 的 except 
                                    pass
                                    
                        st.success(f"🎉 **匯入完成！** 總處理 **{success_p_count}** 筆，全新新增 **{new_product_count}** 筆批價商品！")
                        time.sleep(1)
                        st.rerun()
                else:
                    st.error("❌ 匯入失敗：您的 Excel 欄位不足，找不到 G 欄（成本/批價）。")
            except Exception as e:
                st.error(f"❌ 讀取商品 Excel 發生錯誤: {str(e)}")

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        with st.form("manual_product_form", clear_on_submit=True):
            st.subheader("➕ 手動新增 / 修正單筆商品")
            manual_id = st.text_input("商品編號").strip()
            manual_name = st.text_input("商品名稱").strip()
            manual_unit = st.text_input("規格與銷售注記").strip()
            manual_price = st.number_input("基準批價", min_value=0.0, step=1.0)
            submit_manual_p = st.form_submit_button("儲存 / 更新商品")
            if submit_manual_p and manual_id and manual_name:
                try:
                    supabase.table("product_master").upsert({
                        "product_id": manual_id,
                        "product_name": manual_name,
                        "unit": manual_unit,
                        "price": manual_price
                    }).execute()
                    st.success(f"🎉 成功同步！商品『{manual_name}』已同步！")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 儲存失敗: {str(e)}")

    with col_p2:
        with st.form("delete_product_form", clear_on_submit=True):
            st.subheader("🗑️ 刪除雲端品項")
            del_p_id = st.text_input("輸入要刪除的商品編號").strip()
            st.markdown("---")
            input_pwd_p = st.text_input("🔑 管理員授權密碼", type="password")
            submit_del_p = st.form_submit_button("🔴 確認徹底刪除該商品")
            if submit_del_p and del_p_id:
                if input_pwd_p == ADMIN_PASSWORD:
                    try:
                        supabase.table("product_master").delete().eq("product_id", del_p_id).execute()
                        st.success(f"🗑️ 已成功移除商品代號『{del_p_id}』。")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 刪除失敗: {str(e)}")
                else:
                    st.error("❌ 密碼錯誤：管理員授權認證失敗。")

    st.markdown("---")
    st.subheader("🔍 商品主檔快速智慧查詢看板")
    search_query = st.text_input("💡 請輸入要查詢的『商品名稱關鍵字』或『完整商品編號』：", placeholder="例如：53500 或 雞腿排").strip()
    
    try:
        res_p = supabase.table("product_master").select("*").order("product_id").execute()
        all_prods = res_p.data if res_p.data else []
        
        if search_query and all_prods:
            filtered_prods = [
                p for p in all_prods 
                if search_query.lower() in str(p.get("product_id", "")).lower() or search_query in str(p.get("product_name", ""))
            ]
            if filtered_prods:
                st.success(f"🎯 幫您找到 {len(filtered_prods)} 筆符合關鍵字『{search_query}』的完整資訊：")
                for p in filtered_prods:
                    with st.container():
                        st.markdown(f"### 📦 【{p['product_name']}】")
                        c1, c2, c3 = st.columns(3)
                        with c1: st.metric("🔢 商品編號", str(p['product_id']))
                        with c2: st.metric("💰 基準批價", f"${p['price']} 元")
                        with c3: st.markdown(f"📋 **完整規格、成本與銷售方案：**\n> {p['unit']}")
                        st.markdown("<hr style='border:1px dashed #eee'>", unsafe_allow_html=True)
            else:
                st.warning(f"❌ 查無此商品：找不到任何包含『{search_query}』的編號或品名，請重新確認。")
        
        st.markdown("---")
        st.subheader(f"📋 目前雲端全品項商品總覽 (雲端資料庫實際內存: {len(all_prods)} 筆)")
        if all_prods:
            df_p_show = pd.DataFrame(all_prods)[["product_id", "product_name", "unit", "price"]]
            df_p_show.columns = ["商品編號", "商品名稱", "完整組合注記與成本", "基準批價"]
            st.dataframe(df_p_show, use_container_width=True, hide_index=True)
    except Exception as query_err:
        st.error(f"查詢出錯: {str(query_err)}")
# ====================
# 4. 🏢 管理客戶主檔
# ====================
elif db_mode == "🏢 管理客戶主檔":
    st.header("🏢 客戶主檔雲端資料庫")
    ADMIN_PASSWORD = "123"  

    col1_c, col2_c = st.columns(2)
    with col1_c:
        with st.form("add_customer_form", clear_on_submit=True):
            st.subheader("➕ 快速新增 / 覆蓋更新客戶資料")
            c_id = st.text_input("客戶編號 (必須唯一，例如: XV270041)").strip()
            c_name = st.text_input("官方標準全名 (例如: 御香園食品行)").strip()
            c_shortcut = st.text_input("⭐ 習慣縮寫/自訂快搜 (多個請用逗號隔開)").strip()
            submit_c = st.form_submit_button("儲存 / 更新客戶資料")
            
            if submit_c and c_id and c_name:
                try:
                    existing_kw = ""
                    old_res = supabase.table("customers").select("search_keywords").eq("customer_id", c_id).execute()
                    if old_res.data: existing_kw = old_res.data[0].get("search_keywords", "")
                    
                    learned_part = existing_kw.split(" , ", 1)[1] if "SHORTCUT:" in existing_kw and " , " in existing_kw else existing_kw
                    normalized_shortcut = c_shortcut.replace("，", ",")
                    combined_keywords = f"SHORTCUT:{normalized_shortcut}" + (f" , {learned_part}" if learned_part else "")
                    
                    supabase.table("customers").upsert({"customer_id": c_id, "standard_name": c_name, "search_keywords": combined_keywords}).execute()
                    st.success(f"🎉 客戶『{c_name}』資料已成功更新！")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 寫入失敗: {str(e)}")

    with col2_c:
        with st.form("delete_customer_form", clear_on_submit=True):
            st.subheader("🗑️ 刪除客戶主檔資料")
            d_c_id = st.text_input("要刪除的客戶編號").strip()
            input_pwd_c = st.text_input("🔑 請輸入管理員授權密碼 ", type="password")
            submit_d_c = st.form_submit_button("🔴 確認徹底刪除")
            
            if submit_d_c and d_c_id:
                if input_pwd_c == ADMIN_PASSWORD:
                    try:
                        supabase.table("customers").delete().eq("customer_id", d_c_id).execute()
                        st.success(f"🗑️ 已將客戶編號『{d_c_id}』徹底抹除！")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e: st.error(f"❌ 刪除失敗: {str(e)}")
                else: st.error("❌ 授權密碼錯誤！")

    st.markdown("---")
    st.subheader("📋 目前雲端客戶總覽")
    try:
        res = supabase.table("customers").select("*").order("customer_id").execute()
        if res.data:
            parsed_cust_list = []
            for row in res.data:
                kw = row.get("search_keywords", "")
                shortcut = kw.split(" , ", 1)[0].replace("SHORTCUT:", "").strip() if "SHORTCUT:" in kw else ""
                line_kw = kw.split(" , ", 1)[1] if " , " in kw else ""
                
                parsed_cust_list.append({
                    "客戶編號": row["customer_id"], "官方標準全名": row["standard_name"],
                    "⭐ 自訂快搜/縮寫": shortcut, "🤖 系統已學會的圖片特徵": line_kw if line_kw else "尚未綁定"
                })
            st.dataframe(pd.DataFrame(parsed_cust_list), use_container_width=True, hide_index=True)
        else: st.info("💡 目前雲端內無客戶資料。")
    except Exception as e: st.error(f"❌ 讀取雲端資料失敗: {str(e)}")

# ====================
# 5. 🏪 全廠揀貨理貨大總管
# ====================
elif db_mode == "🏪 全廠揀貨理貨大總管":
    st.title("🏪 全廠揀貨理貨大總管")
    st.caption("🚀 專為現場設計的「純唯讀」揀貨與分貨看板，支援跨日期範圍統計，100% 防呆。")

    st.markdown("### 📅 請選擇理貨備貨日期範圍")
    today_date = datetime.now().date()
    selected_range = st.date_input("請選擇開始與結束日期：", value=(today_date, today_date), key="manager_date_range_input")

    if isinstance(selected_range, tuple) and len(selected_range) == 2:
        start_date, end_date = selected_range
        start_date_str = start_date.strftime("%Y/%m/%d")
        end_date_str = end_date.strftime("%Y/%m/%d")
        range_label = f"{start_date_str}" if start_date == end_date else f"{start_date_str} 至 {end_date_str}"
        st.info(f"🔍 目前正在統計區間：`{range_label}` 的全廠單據資訊")

        raw_orders = []
        if supabase:
            try:
                res_orders = supabase.table("delivery_orders").select("*").gte("delivery_date", start_date_str).lte("delivery_date", end_date_str).eq("status", "已登記未出貨").execute()
                raw_orders = res_orders.data if res_orders.data else []
            except Exception as e:
                st.error(f"❌ 雲端資料庫連線失敗：{str(e)}")

        if not raw_orders:
            st.info(f"✨ 報告大總管：在 `{range_label}` 這段時間內，無任何待出貨品項登記。")
        else:
            df_all = pd.DataFrame(raw_orders)
            try:
                pm_data = supabase.table("product_master").select("product_id", "product_name", "price").execute().data
                df_pm = pd.DataFrame(pm_data) if pm_data else pd.DataFrame()
            except: df_pm = pd.DataFrame()

            if not df_pm.empty and not df_all.empty:
                df_all = pd.merge(df_all, df_pm, on="product_name", how="left")
                df_all["product_id"] = df_all["product_id"].fillna("新商品")
                df_all["price"] = pd.to_numeric(df_all["price"], errors='coerce').fillna(0.0)
            else:
                df_all["product_id"] = "新商品"
                df_all["price"] = 0.0

            df_all["quantity"] = pd.to_numeric(df_all["quantity"], errors='coerce').fillna(0).astype(int)
            df_all["總金額"] = df_all["quantity"] * df_all["price"]

            tab1, tab2 = st.tabs(["📊 視角 A：全廠區間總欠貨加總 (廚房/倉庫備貨專用)", "🚚 視角 B：各客戶欠貨明細表 (司機/現場分貨專用)"])

            with tab1:
                st.markdown(f"#### 🛒 倉庫與廚房總計：`{range_label}` 應拉貨大加總")
                df_summary = df_all.groupby(["product_id", "product_name"]).agg({"quantity": "sum", "總金額": "sum"}).reset_index()
                df_summary.columns = ["商品編號", "商品名稱", "全廠待出總數量", "預估總金額"]
                st.dataframe(df_summary, use_container_width=True, hide_index=True)
                
                col_s1, col_s2 = st.columns(2)
                col_s1.metric("📦 待出貨品項總品類", f"{len(df_summary)} 種")
                col_s2.metric("🔢 待出貨商品總件數", f"{df_summary['全廠待出總數量'].sum()} 件")

                st.download_button(label="📥 下載《全廠總揀貨備貨單 .csv》", data=df_summary.to_csv(index=False).encode('utf-8-sig'), file_name=f"全廠總揀貨單_{range_label.replace('/', '')}.csv", mime="text/csv", use_container_width=True)

            with tab2:
                st.markdown(f"#### 📦 理貨與出車：`{range_label}` 各家客戶細單")
                df_detail = df_all[["delivery_date", "customer_name", "product_id", "product_name", "quantity", "price", "總金額"]].copy()
                df_detail.columns = ["叫貨日期", "客戶名稱", "商品編號", "商品名稱", "待出數量", "單價", "總金額"]
                df_detail = df_detail.sort_values(by=["客戶名稱", "叫貨日期", "商品編號"]).reset_index(drop=True)
                st.dataframe(df_detail, use_container_width=True, hide_index=True)
                st.download_button(label="📥 下載《各客戶分貨明細表 .csv》", data=df_detail.to_csv(index=False).encode('utf-8-sig'), file_name=f"各客戶分貨明細表_{range_label.replace('/', '')}.csv", mime="text/csv", use_container_width=True)
    else:
        st.warning("⏳ 請在上方日期選取器中，再次點選「結束日期」（若查詢單日，請連點兩次）。")

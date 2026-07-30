import re
from typing import List, Tuple

def clean_filename(filename: str) -> Tuple[str, List[str]]:
    """
    Enhanced filename cleaning that also extracts languages.
    Returns (cleaned_filename, detected_languages)
    """
    original = filename
    
    # Extract languages first (simplified for test)
    detected_languages = []
    
    # ========== 0. PREPROCESSING: Handle multi-line and Unicode ==========
    # Replace newlines with spaces
    cleaned = re.sub(r'[\r\n]+', ' ', filename)
    
    # Normalize Unicode characters (convert fancy Unicode to ASCII)
    try:
        cleaned = cleaned.encode('ascii', 'ignore').decode('ascii')
    except UnicodeEncodeError:
        pass
    
    # Remove Unicode arrows and special symbols
    unicode_symbols = r'[»«›‹•·▪▫●○■□◆◇★☆✦✧✩✪✫✬✭✮✯✰✱✲✳✴✵✶✷✸✹✺✻✼✽✾✿❀❁❂❃❄❅❆❇❈❉❊❋❌❍❎❏❐❑❒❓❔❕❖❗❘❙❚❛❜❝❞❟❠❡❢❣❤❥❦❧❨❩❪❫❬❭❮❯❰❱❲❳❴❵❶❷❸❹❺❻❼❽❾❿➀➁➂➃➄➅➆➇➈➉➊➋➌➍➎➏➐➑➒➓➔➕➖➗➘➙➚➛➜➝➞➟➠➡➢➣➤➥➦➧➨➩➪➫➬➭➮➯➰➱➲➳➴➵➶➷➸➹➺➻➼➽➾➿⟀⟁⟂⟃⟄⟅⟆⟇⟈⟉⟊⟋⟌⟍⟎⟏⟐⟑⟒⟓⟔⟕⟖⟗⟘⟙⟚⟛⟜⟝⟞⟟⟠⟡⟢⟣⟤⟥⟦⟧⟨⟩⟪⟫⟬⟭⟮⟯⟰⟱⟲⟳⟴⟵⟶⟷⟸⟹⟺⟻⟼⟽⟾⟿⤀⤁⤂⤃⤄⤅⤆⤇⤈⤉⤊⤋�퍎⤏⤐⤑⤒⤓⤔⤕⤖⤗⤘⤙⤚⤛⤜⤝⤞⤟⤠⤡⤢⤣⤤⤥⤦⤧⤨⤩⤪⤫⤬⤭⤮⤯⤰⤱⤲⤳⤴⤵⤶⤷⤸⤹⤺⤻⤼⤽⤾⤿⥀⥁⥂⥃⥄⥅⥆⥇⥈⥉⥊⥋⥌⥍⥎⥏⥐⥑⥒⥓⥔⥕⥖⥗⥘⥙⥚⥛⥜⥝⥞⥟⥠⥡⥢⥣⥤⥥⥦⥧⥨⥩⥪⥫⥬⥭⥮⥯⥰⥱⥲⥳⥴⥵⥶⥷⥸⥹⥺⥻⥼⥽⥾⥿⦀⦁⦂⦃⦄⦅⦆⦇⦈⦉⦊⦋⦌⦍⦎⦏⦐⦑⦒⦓⦔⦕⦖⦗⦘⦙⦚⦛⦜⦝⦞⦟⦠⦡⦢⦣⦤⦥⦦⦧⦨⦩⦪⦫⦬⦭⦮⦯⦰⦱⦲⦳⦴⦵⦶⦷⦸⦹⦺⦻⦼⦽⦾⦿⧀⧁⧂⧃⧄⧅⧆⧇⧈⧉⧊⧋⧌⧍⧎⧏⧐⧑⧒⧓⧔⧕⧖⧗⧘⧙⧚⧛⧜⧝⧞⧟⧠⧡⧢⧣⧤⧥⧦⧧⧨⧩⧪⧫⧬⧭⧮⧯⧰⧱⧲⧳⧴⧵⧶⧷⧸⧹⧺⧻⧼⧽⧾⧿⨀⨁⨂⨃⨄⨅⨆⨇⨈⨉⨊⨋⨌⨍⨎⨏⨐⨑⨒⨓⨔⨕⨖⨗⨘⨙⨚⨛⨜⨝⨞⨟⨠⨡⨢⨣⨤⨥⨦⨧⨨⨩⨪⨫⨬⨭⨮⨯⨰⨱⨲⨳⨴⨵⨶⨷⨸⨹⨺⨻⨼⨽⨾⨿⩀⩁⩂⩃⩄⩅⩆⩇⩈⩉⩊⩋⩌⩍⩎⩏⩐⩑⩒⩓⩔⩕⩖⩗⩘⩙⩚⩛⩜⩝⩞⩟⩠⩡⩢⩣⩤⩥⩦⩧⩨⩩⩪⩫⩬⩭⩮⩯⩰⩱⩲⩳⩴⩵⩶⩷⩸⩹⩺⩻⩼⩽⩾⩿⪀⪁⪂⪃⪄⪅⪆⪇⪈⪉⪊⪋⪌⪍⪎⪏⪐⪑⪒⪓⪔⪕⪖⪗⪘⪙⪚⪛⪜⪝⪞⪟⪠⪡⪢⪣⪤⪥⪦⪧⪨⪩⪪⪫⪬⪭⪮⪯⪰⪱⪲⪳⪴⪵⪶⪷⪸⪹⪺⪻⪼⪽⪾⪿⫀⫁⫂⫃⫄⫅⫆⫇⫈⫉⫊⫋⫌⫍⫎⫏⫐⫑⫒⫓⫔⫕⫖⫗⫘⫙⫚⫛⫝̸⫝⫞⫟⫠⫡⫢⫣⫤⫥⫦⫧⫨⫩⫪⫫⫬⫭⫮⫯⫰⫱⫲⫳⫴⫵⫶⫷⫸⫹⫺⫻⫼⫽⫾⫿⬀⬁⬂⬃⬄⬅⬆⬇⬈⬉⬊⬋⬌⬍⬎⬏⬐⬑⬒⬓⬔⬕⬖⬗⬘⬙⬚⬛⬜⬝⬞⬟⬠⬡⬢⬣⬤⬥⬦⬧⬨⬩⬪⬫⬬⬭⬮⬯⬰⬱⬲⬳⬴⬵⬶⬷⬸⬹⬺⬻⬼⬽⬾⬿⭀⭁⭂⭃⭄⭅⭆⭇⭈⭉⭊⭋⭌⭍⭎⭏⭐⭁⭂⭃⭄⭅⭆⭇⭈⭉⭊⭋⭌⭍⭎⭏⭐⭑⭒⭓⭔⭕⭖⭗⭘⭙⭚⭛⭜⭝⭞⭟⭠⭡⭢⭣⭤⭥⭦⭧⭨⭩⭪⭫⭬⭭⭮⭯⭰⭱⭲⭳⭴⭵⭶⭷⭸⭹⭺⭻⭼⭽⭾⭿⮀⮁⮂⮃⮄⮅⮆⮇⮈⮉⮊⮋⮌⮍⮎⮏⮐⮑⮒⮓⮔⮕⮖⮗⮘⮙⮚⮛⮜⮝⮞⮟⮠⮡⮢⮣⮤⮥⮦⮧⮨⮩⮪⮫⮬⮭⮮⮯⮰⮱⮲⮳⮴⮵⮶⮷⮸⮹⮺⮻⮼⮽⮾⮿⯀⯁⯂⯃⯄⯅⯆⯇⯈⯉⯊⯋⯌⯍⯎⯏⯐⯑⯒⯓⯔⯕⯖⯗⯘⯙⯚⯛⯜⯝⯞⯟⯠⯡⯢⯣⯤⯥⯦⯧⯨⯩⯪⯫⯬⯭⯮⯯⯰⯱⯲⯳⯴⯵⯶⯷⯸⯹⯺⯻⯼⯽⯾⯿ⰀⰁⰂⰃⰄⰅⰆⰇⰈⰉⰊⰋⰌⰍⰎⰏⰐⰑⰒⰓⰔⰕⰖⰗⰘⰙⰚⰛⰜⰝⰞⰟⰠⰡⰢⰣⰤⰥⰦⰧⰨⰩⰪⰫⰬⰭⰮⰯⰰⰱⰲⰳⰴⰵⰶⰷⰸⰹⰺⰻⰼⰽⰾⰿⲀⲁⲂⲃⲄⲅⲆⲇⲈⲉⲊⲋⲌⲍⲎⲏⲐⲑⲒⲓⲔⲕⲖⲗⲘⲙⲚⲛⲜⲝⲞⲟⲠⲡⲢⲣⲤⲥⲦⲧⲨⲩⲪⲫⲬⲭⲮⲯⲲⲳⲴⲵⲶⲷⲸⲹⲺⲻⲼⲽⲾⲿⳀⳁⳂⳃⳄⳅⳆⳇⳈⳉⳊⳋⳌⳍⳎⳏⳐⳑⳒⳓⳔⳕⳖⳗⳘⳙⳚⳛⳜⳝⳞⳟⳠⳡⳢⳣⳤ⳥⳦⳧⳨⳩⳪ⳫⳬⳭⳮ⳯⳰⳱Ⳳⳳ⳴⳵⳶⳷⳸⳹⳺⳻⳼⳽⳾⳿ⴀⴁⴂⴃⴄⴅⴆⴇⴈⴉⴊⴋⴌⴍⴎⴏⴐⴑⴒⴓⴔⴕⴖⴗⴘⴙⴚⴛⴜⴝⴞⴟⴠⴡⴢⴣⴤⴥ⴦ⴧ⴨⴩⴪⴫⴬ⴭ⴮⴯ⴰⴱⴲⴳⴴⴵⴶⴷⴸⴹⴺⴻⴼⴽⴾⴿⵀⵁⵂⵃⵄⵅⵆⵇⵈⵉⵊⵋⵌⵍⵎᵏᵐᵑᵒᵓᵔᵕᵖᵗᵘᵙᵚᵛᵜᵝᵞᵟᵠᵡᵢᵣᵤᵥᵦᵧᵨᵩᵪᵫᵬᵭᵮᵯᵰᵱᵲᵳᵴᵵᵶᵷᵸᵹᵺᵻᵼᵽᵾᵿⶀⶁⶂⶃⶄⶅⶆⶇⶈⶉⶊⶋⶌⶍⶎⶏⶐⶑⶒⶓⶔⶕⶖ⶗⶘⶙⶚⶛⶜⶝⶞⶟ⶠⶡⶢⶣⶤⶥⶦ⶧ⶨⶩⶪⶫⶬⶭⶮ⶯ⶰⶱⶲⶳⶴⶵⶶ⶷ⶸⶹⶺⶻⶼⶽⶾ⶿︀︁︂︃︄︅︆︇︈︉︊︋︌︍︎️︐︑︒︓︔︕︖︗︘︙︧︨︩︪︫︬︭︠︡︢︣︤︥︦︮︯︰︱︲︳︴︵︶︷︸︹︺︻︼︽︾︿﹀﹁﹂﹃﹄﹅﹆﹇﹈﹉﹊﹋﹌﹍﹎﹏﹐﹑﹒﹔﹕﹖﹗﹘﹙﹚﹛﹜﹝﹞﹟﹠﹡﹢﹣﹤﹥﹦﹨﹩﹪﹫！＂＃＄％＆＇（）＊＋，－．／０１２３４５６７８９：；＜＝＞？＠ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ［＼］＾＿｀ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ｛｜｝～￠￡￢￣￤￥￭￮￯]'
    cleaned = re.sub(unicode_symbols, ' ', cleaned)
    
    # Special handling: Remove @username prefix only at very start when followed by separator
    # This handles filenames like "@THEMZNEW_Spider_Man" where @THEMZNEW is a username prefix
    cleaned = re.sub(r'^@[A-Z0-9_]+[\s\-\:_]+', '', cleaned)
    
    # ========== 1. REMOVE CHANNEL PROMOTIONS ==========
    # Handle [@ChannelName] - format (ENHANCED for multiple occurrences)
    cleaned = re.sub(
        r'\[@[A-Za-z0-9_]+\]\s*[-–—]?\s*',
        '',
        cleaned
    )
    
    # Handle @ChannelName - format (multiple occurrences)
    cleaned = re.sub(
        r'@[A-Za-z0-9_]+\s*[-–—]?\s*',
        '',
        cleaned
    )
    
    # Handle • @ChannelName • - format
    cleaned = re.sub(
        r'[•·]\s*@[A-Za-z0-9_]+\s*[•·]\s*[-–—]?\s*',
        '',
        cleaned
    )
    
    # 2. Strip channel branding prefixes at start
    cleaned = re.sub(
        r'^[•·\s]*[\[\(]?\s*@[A-Za-z0-9_]+\s*[\]\)]?\s*[•·]?\s*[-–—]?\s*',
        ' ',
        cleaned
    )
    
    # 3. Remove Telegram usernames with underscores, dashes, brackets, spaces (ENHANCED)
    # Pattern for @username variations - handle multiple occurrences
    # IMPORTANT: Only remove if followed by separator (space, dash, bracket) to avoid removing @ from filenames
    username_patterns = [
        r'[\s\[\]_-]*@[A-Za-z0-9_]+[\s\[\]\-_:]+',  # @username followed by separator
        r'[\s\[\]_-]*@[A-Za-z0-9-]+[\s\[\]\-_:]+',  # @username-with-dashes followed by separator
        r'[\s\[\]_-]*t\.me/[A-Za-z0-9_]+[\s\[\]_-]*',  # t.me/username
        r'[\s\[\]_-]*telegram\.me/[A-Za-z0-9_]+[\s\[\]_-]*',  # telegram.me/username
        r'[\s\[\]_-]*joinchat/[A-Za-z0-9_-]+[\s\[\]_-]*',  # joinchat/...
    ]
    for pattern in username_patterns:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
    
    # 4. Remove common uploader prefixes and branding (ENHANCED)
    # Only remove if the uploader name is in brackets/parentheses or has specific indicators
    # This prevents removing parts of actual titles like "Spider-Man" -> "Man"
    uploader_patterns = [
        r'^\[[A-Za-z0-9_]+\]\s*[-–—]?\s*',  # [UploaderName] -
        r'^\([A-Za-z0-9_]+\)\s*[-–—]?\s*',  # (UploaderName) -
        r'^\{[A-Za-z0-9_]+\}\s*[-–—]?\s*',  # {UploaderName} -
        r'^[A-Za-z0-9_]+:\s*',  # UploaderName:
        r'\s*[-–—]\s*\[[A-Za-z0-9_]+\]\s*$',  # - [UploaderName] at end
        r'\s*[-–—]\s*\([A-Za-z0-9_]+\)\s*$',  # - (UploaderName) at end
        # Only remove bare uploader names at end if they're all uppercase (common for release groups)
        r'\s*[-–—]\s*[A-Z]{2,}\s*$',  # - UPPERCASE at end
    ]
    for pattern in uploader_patterns:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
    
    # 5. Remove technical tags (ENHANCED)
    technical_tags = [
        r'(?<=\W)(org|AMZN|DDP|DD|NF|AAC|TVDL|WEB-DL|WEBRip|BluRay|HDTV|5\.1|2\.1|2\.0|7\.0|7\.1|5\.0|~|\b\w+kbps\b|x265|x264|HEVC|H\.?264|H\.?265)(?=\W)',
        r'\b(?:EAC3|AC3|DTS|DTS-HD|TrueHD|Atmos|FLAC|MP3|Opus)\b',
        r'\b(?:10bit|8bit|Hi10P)\b',
        r'\b(?:HDR|HDR10|HDR10\+|Dolby\.?Vision|DV)\b',
        r'\b(?:REMUX|REMUXED|REMUX\.?WEB)\b',
        r'\b(?:HQ|HDRip|WEBRip|WEB-DL|BluRay)\b',
    ]
    for pattern in technical_tags:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # 6. Remove language bracket patterns (preserve the content but remove brackets)
    # Remove [Tel + Tam + Mal + Kan] but keep the text
    cleaned = re.sub(r'\[[^\]]*(?:Tel|Tam|Mal|Kan|Hin|Eng|Tamil|Telugu|Malayalam|Kannada|Hindi|English)[^\]]*\]', '', cleaned, flags=re.IGNORECASE)
    
    # 7. Remove ESubs, Subs tags (ENHANCED)
    sub_tags = r'\b(?:ESubs?|Subs?|HC|Subbed|Dubbed|Hardcoded|Softcoded|Forced)\b'
    cleaned = re.sub(sub_tags, '', cleaned, flags=re.IGNORECASE)
    
    # 8. Remove quality tags that might interfere with name detection
    quality_tags = r'\b(?:1080p|720p|480p|4K|2160p|1440p|SD|HD|FHD|UHD)\b'
    cleaned = re.sub(quality_tags, '', cleaned, flags=re.IGNORECASE)
    
    # 9. Remove newlines/tabs
    cleaned = re.sub(r'\\[ntr]', ' ', cleaned)
    
    # 10. Replace dots and underscores with spaces (ENHANCED - preserve extension)
    ext_match = re.search(r'\.[a-z0-9]{3,4}$', cleaned, re.IGNORECASE)
    if ext_match:
        ext = ext_match.group()
        base = cleaned[: -len(ext)]
        # Replace dots and underscores with spaces
        base = re.sub(r'[._]', ' ', base, flags=re.IGNORECASE)
        # Clean up multiple spaces
        base = re.sub(r'\s+', ' ', base)
        cleaned = base.strip() + ext
    else:
        cleaned = re.sub(r'[._]', ' ', cleaned, flags=re.IGNORECASE)
    
    # 11. Remove common unwanted text patterns (ENHANCED for Unicode)
    unwanted_patterns = [
        r'\s*[-–—]\s*Join\s*@\w+\s*',  # - Join @username
        r'\s*[-–—]\s*Join\s+\w+\s*',  # - Join channel (without @)
        r'\s*[-–—]\s*Subscribe\s*',  # - Subscribe
        r'\s*[-–—]\s*Follow\s*',  # - Follow
        r'\s*[-–—]\s*Like\s*',  # - Like
        r'\s*[-–—]\s*Share\s*',  # - Share
        r'\s*[-–—]\s*Telegram\s*',  # - Telegram
        r'\s*[-–—]\s*Channel\s*',  # - Channel
        r'\s*[-–—]\s*Main\s+channel\s*',  # - Main channel
        r'\s*[-–—]\s*Official\s+channel\s*',  # - Official channel
        r'\s*\(\s*Join\s*\)',  # (Join)
        r'\s*\(\s*Subscribe\s*\)',  # (Subscribe)
        r'\b(?:www\.|https?://|ftp://)[^\s]+',  # URLs
        r'\b(?:joinchat|t\.me|telegram\.me)/[^\s]+',  # Telegram links
    ]
    for pattern in unwanted_patterns:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
    
    # 12. Clean up multiple spaces and trim
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # 13. Remove any remaining non-printable or special characters (ENHANCED)
    # Keep letters, numbers, spaces, hyphens, parentheses, brackets, apostrophes
    cleaned = re.sub(r'[^\w\s\-\(\)\[\]\.\']', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # 14. Remove leading/trailing special characters
    cleaned = re.sub(r'^[\s\-\.\[\]\(\)]+', '', cleaned)
    cleaned = re.sub(r'[\s\-\.\[\]\(\)]+$', '', cleaned)
    
    # 15. Final cleanup of multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    return cleaned, detected_languages


def extract_title_from_caption(caption: str, file_name: str) -> str:
    """Simulate the caption extraction logic from start.py"""
    USE_CAPTION = True  # Simulating Config.USE_CAPTION = True
    
    if caption and USE_CAPTION:
        title = None
        caption_lines = caption.split("\n")
        
        # First pass: look for lines with video extensions
        for line in caption_lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Remove leading emojis and special characters
            cleaned_line = re.sub(r'^[\W_]+', '', stripped)
            # Check for video extensions
            if re.search(r'\.(mkv|mp4|avi|mov|ts|wmv|m4v|webm|flv|3gp)\b', cleaned_line, re.IGNORECASE):
                title = cleaned_line
                break
        
        # Second pass: if no extension found, look for movie-like patterns (year, quality, etc.)
        if not title:
            for line in caption_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                cleaned_line = re.sub(r'^[\W_]+', '', stripped)
                # Look for patterns like "Movie Name (2021)" or "Movie Name 720p"
                if re.search(r'\(\d{4}\)|\b(1080p|720p|480p|4K|2160p|1440p)\b', cleaned_line, re.IGNORECASE):
                    title = cleaned_line
                    break
        
        # Third pass: use the first substantial line if still no match
        if not title:
            for line in caption_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                cleaned_line = re.sub(r'^[\W_]+', '', stripped)
                # Skip lines that are just hashtags, URLs, or very short
                if len(cleaned_line) > 5 and not cleaned_line.startswith('#') and not cleaned_line.startswith('http'):
                    title = cleaned_line
                    break
        
        # If we got a title from caption but it doesn't have extension, append filename extension
        if title and not re.search(r'\.(mkv|mp4|avi|mov|ts|wmv|m4v|webm|flv|3gp)\b', title, re.IGNORECASE):
            if file_name:
                ext_match = re.search(r'\.[a-z0-9]{3,4}$', file_name, re.IGNORECASE)
                if ext_match:
                    title = title + ext_match.group()
        
        # Final fallback to filename
        if not title:
            title = file_name
    else:
        title = file_name
    
    return title


# Test with actual data
filename = "@THEMZNEW_Spider_Man:_No_Way_Home_2021_HQ_Blu_Ray_720p_DD5_1_192Kbps.mkv"
caption = """[@CloudMovieX]
Spider-Man: No Way Home (2021) HQ Blu-Ray - 720p - (DD5.1 - 192Kbps) [#Telugu + #Tamil + #Hindi + #English] 1.8GB ESub.mkv

🎬 720p x264 | ⏳ 02:28:09
🔊 English, Hindi, Tamil, Telugu
💬 English"""

print("=" * 80)
print("Testing with ACTUAL filename and caption")
print("=" * 80)

print(f"\nFilename: {filename}")
print(f"\nCaption:\n{caption}")

# Step 1: Extract title from caption (simulating USE_CAPTION=True)
print("\n" + "=" * 80)
print("STEP 1: Extract title from caption")
print("=" * 80)
title_from_caption = extract_title_from_caption(caption, filename)
print(f"Title from caption: {title_from_caption}")

# Step 2: Clean the extracted title
print("\n" + "=" * 80)
print("STEP 2: Clean the extracted title")
print("=" * 80)
cleaned_title, detected_langs = clean_filename(title_from_caption)
print(f"Cleaned title: {cleaned_title}")
print(f"Detected languages: {detected_langs}")

# Step 3: Also test cleaning the filename directly (for comparison)
print("\n" + "=" * 80)
print("STEP 3: Clean filename directly (for comparison)")
print("=" * 80)
cleaned_filename, _ = clean_filename(filename)
print(f"Cleaned filename: {cleaned_filename}")

# Verification
print("\n" + "=" * 80)
print("VERIFICATION")
print("=" * 80)
if "Spider-Man" in cleaned_title or "Spider Man" in cleaned_title:
    print("✓ SUCCESS: 'Spider-Man' preserved in cleaned title from caption")
else:
    print("✗ FAILURE: 'Spider-Man' lost in cleaned title from caption")

if "No Way Home" in cleaned_title:
    print("✓ SUCCESS: 'No Way Home' preserved in cleaned title from caption")
else:
    print("✗ FAILURE: 'No Way Home' lost in cleaned title from caption")

if "2021" in cleaned_title:
    print("✓ SUCCESS: Year '2021' preserved in cleaned title from caption")
else:
    print("✗ FAILURE: Year '2021' lost in cleaned title from caption")

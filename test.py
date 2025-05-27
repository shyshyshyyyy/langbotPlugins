"""
增强版网盘资源搜索插件 - LangBot插件
增加了收藏、历史记录、热门推荐等功能
"""

import requests
import json
import re
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from langbot.plugin import Plugin
from langbot.events import *

class EnhancedNetdiskSearchPlugin(Plugin):
    
    def __init__(self):
        super().__init__()
        self.name = "enhanced_netdisk_search"
        self.description = "增强版网盘资源搜索插件"
        self.version = "2.0.0"
        
        # API配置
        self.api_base_url = "https://so.yuneu.com"
        self.search_endpoint = "/open/search/disk"
        
        # 搜索相关配置
        self.search_triggers = ["搜索", "找资源", "下载", "资源", "search", "find"]
        self.file_types = {
            "电影": "video", "视频": "video", "影片": "video",
            "软件": "software", "程序": "software", "应用": "software",
            "文档": "document", "资料": "document", "教程": "document",
            "图片": "image", "照片": "image", "壁纸": "image",
            "音乐": "audio", "歌曲": "audio", "音频": "audio",
            "压缩包": "archive", "安装包": "archive"
        }
        self.time_filters = {
            "今天": "today", "本周": "week", "本月": "month", "本年": "year"
        }
        
        # 数据库初始化
        self.db_path = "./data/netdisk_search.db"
        self._init_database()
        
        # 缓存配置
        self.search_cache = {}
        self.cache_expire_time = 1800  # 30分钟
        
    def _init_database(self):
        """初始化数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 创建搜索历史表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    file_type TEXT,
                    results_count INTEGER,
                    search_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 创建用户收藏表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    resource_title TEXT NOT NULL,
                    resource_info TEXT,
                    add_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 创建热门搜索表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS popular_searches (
                    query TEXT PRIMARY KEY,
                    search_count INTEGER DEFAULT 1,
                    last_search TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self.logger.error(f"数据库初始化失败: {str(e)}")
    
    async def on_message(self, event: MessageEvent):
        """处理消息事件"""
        message_text = event.message_text.strip()
        user_id = str(event.sender_id)
        
        # 检查特殊命令
        if message_text.startswith("我的收藏") or message_text.startswith("收藏列表"):
            await self._show_user_favorites(event, user_id)
            return
        elif message_text.startswith("搜索历史") or message_text.startswith("历史记录"):
            await self._show_search_history(event, user_id)
            return
        elif message_text.startswith("热门搜索") or message_text.startswith("热门资源"):
            await self._show_popular_searches(event)
            return
        elif message_text.startswith("收藏"):
            await self._handle_favorite_command(event, user_id, message_text)
            return
        
        # 检查是否包含搜索触发词
        if not any(trigger in message_text for trigger in self.search_triggers):
            return
            
        # 提取搜索参数
        search_data = self._extract_search_query(message_text)
        if not search_data[0]:
            await self._send_help_message(event)
            return
            
        search_query, file_type, time_filter, exact_match = search_data
        
        try:
            # 检查缓存
            cache_key = f"{search_query}_{file_type}_{time_filter}_{exact_match}"
            cached_result = self._get_cached_result(cache_key)
            
            if cached_result:
                results = cached_result
                cache_hit = True
            else:
                # 调用API搜索
                results = await self._search_resources(search_query, file_type, time_filter, exact_match)
                self._cache_result(cache_key, results)
                cache_hit = False
            
            # 记录搜索历史
            self._record_search_history(user_id, search_query, file_type, len(results))
            
            if results:
                response = self._format_search_results(results, search_query, cache_hit)
            else:
                response = f"抱歉，没有找到关于「{search_query}」的资源 😔\n\n💡 建议：\n• 尝试更简单的关键词\n• 检查拼写是否正确\n• 查看热门搜索获取灵感"
                
            await event.reply(response)
            
        except Exception as e:
            await event.reply("搜索出现问题，请稍后再试 🔧")
            self.logger.error(f"搜索错误: {str(e)}")
    
    def _extract_search_query(self, message: str) -> tuple:
        """从消息中提取搜索关键词和筛选条件"""
        query_text = message
        for trigger in self.search_triggers:
            if trigger in query_text:
                query_text = query_text.replace(trigger, "").strip()
                break
        
        # 提取文件类型筛选
        file_type = ""
        for type_name, type_code in self.file_types.items():
            if type_name in query_text:
                file_type = type_code
                query_text = query_text.replace(type_name, "").strip()
                break
        
        # 提取时间筛选
        time_filter = ""
        for time_name, time_code in self.time_filters.items():
            if time_name in query_text:
                time_filter = time_code
                query_text = query_text.replace(time_name, "").strip()
                break
        
        # 检测精确匹配
        exact_match = False
        if '"' in query_text or '"' in query_text:
            exact_match = True
            query_text = query_text.replace('"', '').replace('"', '').strip()
        
        # 清理查询文本
        query_text = re.sub(r'[：:，,。！!？?]', '', query_text).strip()
        
        return query_text, file_type, time_filter, exact_match
    
    async def _search_resources(self, query: str, file_type: str = "", time_filter: str = "", exact_match: bool = False) -> List[Dict]:
        """调用网站搜索API"""
        try:
            payload = {
                'q': query,
                'page': 1,
                'size': 8,  # 增加结果数量
                'time': time_filter,
                'type': file_type,
                'exact': exact_match
            }
            
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'LangBot-Enhanced-NetDisk/2.0',
                'Accept': 'application/json'
            }
            
            response = requests.post(
                f"{self.api_base_url}{self.search_endpoint}",
                json=payload,
                headers=headers,
                timeout=20
            )
            
            if response.status_code == 200:
                data = response.json()
                results = self._extract_results_from_response(data)
                
                # 更新热门搜索统计
                self._update_popular_search(query)
                
                return results
            else:
                self.logger.error(f"API请求失败: {response.status_code}")
                return []
                
        except Exception as e:
            self.logger.error(f"搜索请求异常: {str(e)}")
            return []
    
    def _extract_results_from_response(self, data: dict) -> List[Dict]:
        """从API响应中提取结果"""
        if not isinstance(data, dict):
            return []
            
        # 尝试多种可能的数据结构
        possible_paths = [
            ['data', 'list'],
            ['data', 'items'],
            ['results'],
            ['items'],
            ['list'],
            ['data']
        ]
        
        for path in possible_paths:
            current = data
            try:
                for key in path:
                    current = current[key]
                if isinstance(current, list):
                    return current
            except (KeyError, TypeError):
                continue
        
        return []
    
    def _format_search_results(self, results: List[Dict], query: str, cache_hit: bool = False) -> str:
        """格式化搜索结果"""
        if not results:
            return f"没有找到关于「{query}」的资源 😔"
            
        cache_indicator = " (缓存)" if cache_hit else ""
        response = f"🔍 找到 {len(results)} 个关于「{query}」的资源{cache_indicator}：\n\n"
        
        for i, item in enumerate(results[:6], 1):
            title = item.get('name', item.get('title', item.get('filename', '未知标题')))
            file_size = item.get('size', item.get('fileSize', 0))
            source = item.get('source', item.get('platform', item.get('disk', '')))
            file_type = item.get('type', item.get('fileType', ''))
            update_time = item.get('updateTime', item.get('time', ''))
            
            # 格式化文件大小
            size_str = self._format_file_size(file_size)
            
            # 选择emoji
            emoji = self._get_file_emoji(file_type, title)
            
            response += f"{emoji} {i}. {title}\n"
            response += f"   📦 {size_str}"
            if source:
                response += f" | 🌐 {source}"
            if update_time:
                response += f" | 🕒 {update_time}"
            response += "\n"
            
            # 添加操作提示
            response += f"   💾 获取{i} | ⭐ 收藏{i}\n\n"
        
        # 添加功能说明
        response += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        response += "🎛️ 快捷操作:\n"
        response += "• 获取[序号] - 获取下载信息\n"
        response += "• 收藏[序号] - 收藏到个人列表\n"
        response += "• 我的收藏 - 查看收藏列表\n"
        response += "• 搜索历史 - 查看搜索记录\n"
        response += "• 热门搜索 - 查看热门资源\n"
        response += f"• 🌐 完整网站: {self.api_base_url.replace('/open', '')}"
        
        return response
    
    def _format_file_size(self, size) -> str:
        """格式化文件大小"""
        if isinstance(size, (int, float)) and size > 0:
            if size >= 1024**3:  # GB
                return f"大小: {size / (1024**3):.1f} GB"
            elif size >= 1024**2:  # MB
                return f"大小: {size / (1024**2):.1f} MB"
            elif size >= 1024:  # KB
                return f"大小: {size / 1024:.1f} KB"
            else:
                return f"大小: {size} B"
        else:
            return "大小: 未知"
    
    def _get_file_emoji(self, file_type: str, title: str) -> str:
        """根据文件类型返回emoji"""
        title_lower = title.lower()
        file_type_lower = file_type.lower() if file_type else ""
        
        emoji_map = {
            'video': "🎬", 'audio': "🎵", 'image': "🖼️",
            'archive': "📦", 'software': "💻", 'document': "📄"
        }
        
        # 先检查文件类型
        if file_type_lower in emoji_map:
            return emoji_map[file_type_lower]
        
        # 再检查文件扩展名
        for ext_group, emoji in [
            (['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv'], "🎬"),
            (['.mp3', '.flac', '.wav', '.aac', '.m4a'], "🎵"),
            (['.jpg', '.png', '.gif', '.bmp', '.webp'], "🖼️"),
            (['.zip', '.rar', '.7z', '.tar', '.gz'], "📦"),
            (['.exe', '.msi', '.dmg', '.deb', '.apk'], "💻"),
            (['.pdf', '.doc', '.docx', '.txt', '.ppt'], "📄")
        ]:
            if any(ext in title_lower for ext in ext_group):
                return emoji
        
        return "📁"
    
    def _get_cached_result(self, cache_key: str) -> Optional[List[Dict]]:
        """获取缓存结果"""
        if cache_key in self.search_cache:
            cached_data, cache_time = self.search_cache[cache_key]
            if datetime.now().timestamp() - cache_time < self.cache_expire_time:
                return cached_data
            else:
                del self.search_cache[cache_key]
        return None
    
    def _cache_result(self, cache_key: str, results: List[Dict]):
        """缓存搜索结果"""
        self.search_cache[cache_key] = (results, datetime.now().timestamp())
    
    def _record_search_history(self, user_id: str, query: str, file_type: str, results_count: int):
        """记录搜索历史"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO search_history (user_id, query, file_type, results_count)
                VALUES (?, ?, ?, ?)
            ''', (user_id, query, file_type, results_count))
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"记录搜索历史失败: {str(e)}")
    
    def _update_popular_search(self, query: str):
        """更新热门搜索统计"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO popular_searches (query, search_count, last_search)
                VALUES (?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(query) DO UPDATE SET
                search_count = search_count + 1,
                last_search = CURRENT_TIMESTAMP
            ''', (query,))
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"更新热门搜索失败: {str(e)}")
    
    async def _show_search_history(self, event, user_id: str):
        """显示用户搜索历史"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT query, file_type, results_count, search_time
                FROM search_history
                WHERE user_id = ?
                ORDER BY search_time DESC
                LIMIT 10
            ''', (user_id,))
            
            history = cursor.fetchall()
            conn.close()
            
            if not history:
                await event.reply("📝 您还没有搜索历史记录")
                return
            
            response = "📝 您的搜索历史 (最近10条)：\n\n"
            for i, (query, file_type, count, search_time) in enumerate(history, 1):
                type_str = f"[{file_type}]" if file_type else ""
                response += f"{i}. {query} {type_str}\n"
                response += f"   📊 找到 {count} 个结果 | 🕒 {search_time[:16]}\n\n"
            
            response += "💡 直接回复序号可以重新执行该搜索"
            await event.reply(response)
            
        except Exception as e:
            await event.reply("获取搜索历史失败 😔")
            self.logger.error(f"获取搜索历史错误: {str(e)}")
    
    async def _show_popular_searches(self, event):
        """显示热门搜索"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT query, search_count, last_search
                FROM popular_searches
                WHERE last_search > datetime('now', '-7 days')
                ORDER BY search_count DESC
                LIMIT 10
            ''', )
            
            popular = cursor.fetchall()
            conn.close()
            
            if not popular:
                await event.reply("📊 暂无热门搜索数据")
                return
            
            response = "🔥 热门搜索 (最近7天)：\n\n"
            for i, (query, count, last_search) in enumerate(popular, 1):
                fire_emoji = "🔥" if count >= 10 else "⭐" if count >= 5 else "💫"
                response += f"{fire_emoji} {i}. {query}\n"
                response += f"   📊 搜索 {count} 次 | 🕒 {last_search[:10]}\n\n"
            
            response += "💡 点击感兴趣的关键词直接搜索"
            await event.reply(response)
            
        except Exception as e:
            await event.reply("获取热门搜索失败 😔")
            self.logger.error(f"获取热门搜索错误: {str(e)}")
    
    async def _show_user_favorites(self, event, user_id: str):
        """显示用户收藏列表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT resource_title, resource_info, add_time
                FROM user_favorites
                WHERE user_id = ?
                ORDER BY add_time DESC
                LIMIT 20
            ''', (user_id,))
            
            favorites = cursor.fetchall()
            conn.close()
            
            if not favorites:
                await event.reply("⭐ 您还没有收藏任何资源\n\n💡 搜索时回复 \"收藏[序号]\" 可以收藏资源")
                return
            
            response = f"⭐ 您的收藏列表 (共{len(favorites)}个)：\n\n"
            for i, (title, info, add_time) in enumerate(favorites, 1):
                response += f"⭐ {i}. {title}\n"
                if info:
                    response += f"   {info}\n"
                response += f"   🕒 {add_time[:16]}\n\n"
            
            await event.reply(response)
            
        except Exception as e:
            await event.reply("获取收藏列表失败 😔")
            self.logger.error(f"获取收藏列表错误: {str(e)}")
    
    async def _handle_favorite_command(self, event, user_id: str, message: str):
        """处理收藏命令"""
        # 这里需要实现收藏功能的具体逻辑
        # 由于需要获取之前搜索的结果，可能需要额外的状态管理
        await event.reply("收藏功能正在开发中... 🚧")
    
    async def _send_help_message(self, event):
        """发送帮助信息"""
        help_text = """
🤖 增强版网盘资源搜索助手 (so.yuneu.com)

🔍 基础搜索:
• 搜索 电影名称
• 找资源 软件名称  
• 下载 文档名称

🎯 高级搜索:
• 搜索 电影 复仇者联盟 (按类型)
• 搜索 本月 Python教程 (按时间)
• 搜索 "Python 3.9" (精确匹配)

📋 个人功能:
• 我的收藏 - 查看收藏的资源
• 搜索历史 - 查看搜索记录
• 热门搜索 - 查看热门资源

📂 支持类型:
电影、视频、软件、程序、文档、图片、音乐、压缩包

⏰ 时间筛选:
今天、本周、本月、本年

💡 实用技巧:
• 关键词要具体明确
• 组合多个搜索条件
• 使用引号精确搜索
• 善用收藏和历史功能

🌐 完整网站: https://so.yuneu.com
        """
        await event.reply(help_text.strip())

# 插件实例
plugin = EnhancedNetdiskSearchPlugin()
# encoding:utf-8
import json
import os
import html
from typing import List
import os
import uuid

import requests
from pydub import AudioSegment

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.tmp_dir import TmpDir
from plugins import *

@plugins.register(
    name="Podcast",
    desire_priority=10,
    hidden=False,
    desc="Generate podcast from url using OpenAI API",
    version="0.1.0",
    author="hanfangyuan",
)
class PodcastPlugin(Plugin):

    jina_reader_base = "https://r.jina.ai"
    openai_api_base = "https://api.openai.com/v1"
    openai_llm_model = "gpt-4o"
    openai_audio_model = "tts-1"
    max_words = 50000
    white_url_list = []
    black_url_list = [
        "https://support.weixin.qq.com", # 视频号视频
        "https://channels-aladin.wxqcloud.qq.com", # 视频号音乐
    ]

    def __init__(self):
        super().__init__()
        try:
            self.config = super().load_config()
            if not self.config:
                self.config = self._load_config_template()
            self.jina_reader_base = self.config.get("jina_reader_base", self.jina_reader_base)
            self.openai_api_base = self.config.get("openai_api_base", self.openai_api_base)
            self.openai_api_key = self.config.get("openai_api_key", "")
            self.openai_llm_model = self.config.get("openai_llm_model", self.openai_llm_model)
            self.openai_audio_model = self.config.get("openai_audio_model", self.openai_audio_model)
            self.max_words = self.config.get("max_words", self.max_words)
            self.white_url_list = self.config.get("white_url_list", self.white_url_list)
            self.black_url_list = self.config.get("black_url_list", self.black_url_list)
            self.podcast = Podcast(self.openai_api_base, self.openai_api_key, self.openai_llm_model, self.openai_audio_model, TmpDir().path())
            logger.info(f"[Podcast] inited, config={self.config}")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        except Exception as e:
            logger.error(f"[Podcast] 初始化异常：{e}")
            raise "[Podcast] init failed, ignore "

    def on_handle_context(self, e_context: EventContext):
        try:
            context = e_context["context"]
            content = context.content
            if context.type != ContextType.SHARING and context.type != ContextType.TEXT:
                return
            if not content.startswith("/podcast"):
                return
            url = content.replace("/podcast", "").strip()
            if not self._check_url(url):
                reply = Reply(ReplyType.ERROR, f"{url} is not a valid url")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return

            logger.debug("[Podcast] on_handle_context. content: %s" % url)
            reply = Reply(ReplyType.TEXT, "🎉正在为您生成播客，请稍候...")
            channel = e_context["channel"]
            channel.send(reply, context)
            
            text = self.parse_url(url)
            podcast_file_path = self.podcast.generate_podcast(text[:self.max_words])
            reply = Reply(ReplyType.FILE, podcast_file_path)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

        except Exception as e:
            logger.exception(f"[Podcast] {str(e)}")
            reply = Reply(ReplyType.ERROR, "我暂时无法生成播客，请稍后再试")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def get_help_text(self, verbose, **kwargs):
        return f'使用jina reader和ChatGPT把网页链接转为播客。使用 /podcast+<url> 生成播客，例如 /postcast https://example.com/example.html'

    def parse_url(self, url: str, retry_count: int = 3):
        try:
            target_url = html.unescape(url) # 解决公众号卡片链接校验问题，参考 https://github.com/fatwang2/sum4all/commit/b983c49473fc55f13ba2c44e4d8b226db3517c45
            jina_url = self.jina_reader_base + "/" + target_url
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
            response = requests.get(jina_url, headers=headers, timeout=60)
            response.raise_for_status()
            return response.text
        except Exception as e:
            if retry_count > 0:
                print(f"[Podcast] Error: {str(e)}, retrying... Remaining retries: {retry_count}")
                return self.parse_url(url, retry_count - 1)
            raise e
    def _load_config_template(self):
        logger.debug("No Suno plugin config.json, use plugins/podcast/config.json.template")
        try:
            plugin_config_path = os.path.join(self.path, "config.json.template")
            if os.path.exists(plugin_config_path):
                with open(plugin_config_path, "r", encoding="utf-8") as f:
                    plugin_conf = json.load(f)
                    return plugin_conf
        except Exception as e:
            logger.exception(e)
    def _check_url(self, target_url: str):
        stripped_url = target_url.strip()
        # 简单校验是否是url
        if not stripped_url.startswith("http://") and not stripped_url.startswith("https://"):
            return False

        # 检查白名单
        if len(self.white_url_list):
            if not any(stripped_url.startswith(white_url) for white_url in self.white_url_list):
                return False

        # 排除黑名单，黑名单优先级>白名单
        for black_url in self.black_url_list:
            if stripped_url.startswith(black_url):
                return False

        return True

podcast_prompt_template = """
# 任务
把一段文本转换成男女对话的播客形式。
# 要求
1. 女主播为Alice, 男主播为Bob
2. A首先对观众问好，然后对文本进行总结概括，引出本篇播客要讨论的内容
3. Alice负责提问、回应和补充，Bob负责回答和陈述观点
4. 对话自然流畅，风格轻松随意
5. 生成的播客对话必须完整详细地展现输入文本的内容，不要遗漏任何信息
# 格式
你必须使用 <podcast></podcast> XML 标签中的格式输出播客内容，除此之外，不要输出其他文字
<podcast>
Alice或Bob: 对话内容
Alice或Bob: 对话内容
</podcast>
# 输出示例
Alice: 对观众问好，然后对文本进行总结概括，引出本篇播客要讨论的内容
Bob: 和观众问好
Alice: 提问
Bob: 回答
Alice: ...
Bob: ...
...
# 输入文本:
下方<input-text></input-text> XML 标签中的内容为需要转换为播客的文本
<input-text>
{}
</input-text>
"""

class PodcastSegment:
    def __init__(self, speaker: str, text: str, audio_path: str = ""):
        self.speaker = speaker
        self.text = text
        self.audio_path = audio_path

class Podcast:
    def __init__(self, openai_api_base: str, openai_api_key: str, llm_model: str, audio_model: str, audio_dir: str):
        self.openai_api_base = openai_api_base
        self.openai_api_key = openai_api_key
        self.llm_model = llm_model
        self.audio_model = audio_model
        self.audio_dir = audio_dir

    def openai_chat(self, prompt: str, retry_count: int = 3) -> str:
        """
        使用OpenAI API进行聊天对话。

        :param prompt: 发送给API的提示文本
        :param retry_count: 重试次数，默认为3
        :return: API返回的响应文本
        """
        chat_url = self.openai_api_base + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}"
        }
        messages = [
            {"role": "system", "content": prompt}
        ]
        payload = {
            "model": self.llm_model,
            "messages": messages
        }
        try:
            response = requests.post(chat_url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()['choices'][0]['message']['content']
            return result
        except Exception as e:
            if retry_count > 0:
                print(f"[Podcast] err: {str(e)}, retrying..., remaining retry count: {retry_count}")
                return self.openai_chat(prompt, retry_count - 1)
            raise e

    def openai_audio(self, input_text: str, voice: str, retry_count: int = 3) -> bytes:
        """
        使用OpenAI API生成音频。

        :param input_text: 要转换为语音的文本
        :param voice: 要使用的语音模型
        :param retry_count: 重试次数，默认为3
        :return: 生成的音频内容
        """
        audio_url = self.openai_api_base + "/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.audio_model,
            "input": input_text,
            "voice": voice
        }
        
        try:
            response = requests.post(audio_url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            return response.content  # 返回音频内容
        except Exception as e:
            if retry_count > 0:
                print(f"[Audio Generation] Error: {str(e)}, retrying... Remaining retries: {retry_count}")
                return self.openai_audio(input_text, voice, retry_count - 1)
            raise e

    def generate_podcast_texts(self, input_text: str) -> List[PodcastSegment]:
        """
        利用输入文本生成播客的文本段列表。

        :param input_text: 用于生成播客内容的输入文本
        :return: PodcastSegment对象的列表，每个对象包含说话者和对应的文本
        """
        prompt = podcast_prompt_template.format(input_text)
        podcast_text = self.openai_chat(prompt)
        segments = []
        lines = podcast_text.strip().split('\n')
        for line in lines:
            if ':' in line:
                speaker, text = line.split(':', 1)
                speaker = speaker.strip()
                text = text.strip()
                segments.append(PodcastSegment(speaker, text))
        return segments

    # TODO: 并发生成音频
    def generate_podcast_audios(self, segments: List[PodcastSegment]) -> List[PodcastSegment]:
        """
        为播客的文本段生成对应的音频文件。

        :param segments: PodcastSegment对象的列表，包含说话者和文本信息
        :return: 更新后的PodcastSegment对象列表，每个对象包含生成的音频文件路径
        """
        for segment in segments:
            voice = "onyx" if "bob" in segment.speaker.lower() else "nova"
            print(f"[Podcast] Generating audio for {segment.speaker}: {segment.text}")
            audio_content = self.openai_audio(segment.text, voice)
            audio_path = os.path.join(self.audio_dir, f"podcast_seg_{str(uuid.uuid4())[:18]}.mp3")
            with open(audio_path, "wb") as f:
                f.write(audio_content)
            segment.audio_path = audio_path
        return segments

    def merge_podcast_audios(self, segments: List[PodcastSegment]) -> str:
        """
        合并PodcastSegment列表中的音频为一个完整的音频文件。

        :param segments: PodcastSegment对象的列表，每个对象包含音频文件路径
        :return: 合并后的音频文件路径
        """
        # 初始化一个空音频段
        combined = AudioSegment.empty()
        module_dir = os.path.dirname(os.path.abspath(__file__))
        prefix_audio = AudioSegment.from_mp3(os.path.join(module_dir, "podcast_prefix.mp3"))
        suffix_audio = AudioSegment.from_mp3(os.path.join(module_dir, "podcast_suffix.mp3"))
        combined += prefix_audio
        # 遍历PodcastSegment列表，合并音频
        for segment in segments:
            if os.path.exists(segment.audio_path):
                audio = AudioSegment.from_mp3(segment.audio_path)
                combined += audio
        combined += suffix_audio
        # 导出合并后的音频文件
        print(f"[Podcast] Merging {len(segments)} audio files...")
        merged_audio_path = os.path.join(self.audio_dir, f"podcast_{str(uuid.uuid4())[:18]}.mp3")
        combined.export(merged_audio_path, format="mp3")
        # 删除 segments
        print("[Podcast] Deleting segments...")
        for segment in segments:
            os.remove(segment.audio_path)
        return merged_audio_path

    def generate_podcast(self, text: str) -> str:
        """
        生成完整的播客音频文件。

        :param text: 用于生成播客内容的输入文本
        :return: 生成的播客音频文件路径
        """
        text_segments = self.generate_podcast_texts(text)
        audio_segments = self.generate_podcast_audios(text_segments)
        merged_audio_path = self.merge_podcast_audios(audio_segments)
        return merged_audio_path

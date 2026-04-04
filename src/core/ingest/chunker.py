from typing import Dict, Any, List
import re
from loguru import logger

class DocumentChunker:
    def __init__(self, chunk_size: int = 384, overlap: int = 128):
        self.chunk_size = chunk_size
        self.overlap = overlap
    
    def chunk(self, cleaned_content: Dict[str, Any]) -> List[Dict[str, Any]]:
        """将清洗后的文档内容切割成 chunks"""
        try:
            pages = cleaned_content.get("pages", [])
            chunks = []
            
            for page in pages:
                page_text = page.get("text", "")
                page_num = page.get("page_num", 1)
                
                # 按段落分割
                paragraphs = re.split(r'\n\s*\n', page_text)
                current_chunk = []
                current_length = 0
                
                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    
                    para_length = len(para.split())  # 按词数计算
                    
                    # 如果当前 chunk 加上新段落超过限制，保存当前 chunk 并开始新 chunk
                    if current_length + para_length > self.chunk_size:
                        # 保存当前 chunk
                        if current_chunk:
                            chunk_text = ' '.join(current_chunk)
                            chunks.append({
                                "content": chunk_text,
                                "content_type": "text",
                                "metadata": {
                                    "page_num": page_num,
                                    "chunk_type": "paragraph"
                                }
                            })
                        
                        # 开始新 chunk，包含重叠部分
                        if self.overlap > 0 and current_chunk:
                            # 计算需要重叠的词数
                            overlap_words = ' '.join(current_chunk).split()[-self.overlap:]
                            current_chunk = [' '.join(overlap_words), para]
                            current_length = len(overlap_words) + para_length
                        else:
                            current_chunk = [para]
                            current_length = para_length
                    else:
                        current_chunk.append(para)
                        current_length += para_length
                
                # 处理最后一个 chunk
                if current_chunk:
                    chunk_text = ' '.join(current_chunk)
                    chunks.append({
                        "content": chunk_text,
                        "content_type": "text",
                        "metadata": {
                            "page_num": page_num,
                            "chunk_type": "paragraph"
                        }
                    })
            
            logger.info(f"文档切片完成: 生成 {len(chunks)} 个 chunk")
            return chunks
            
        except Exception as e:
            logger.error(f"文档切片失败: {str(e)}")
            return []

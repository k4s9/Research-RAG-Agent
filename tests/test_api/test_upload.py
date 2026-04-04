import requests
import json
import os

# API 基础 URL
API_BASE_URL = "http://localhost:8002/api/v1"

# 测试文件路径
test_file_path = "./data/pi0.pdf"

# 确保测试文件存在
if not os.path.exists(test_file_path):
    print(f"测试文件不存在: {test_file_path}")
    exit(1)

# 构建项目 ID 列表
project_ids = ["proj-1", "proj-2"]

# 构建文件对象
files = {
    "file": (
        os.path.basename(test_file_path),
        open(test_file_path, "rb"),
        "application/pdf",
    )
}
data = {"project_ids": json.dumps(project_ids), "description": "测试文档"}

print("开始测试文档上传...")
try:
    response = requests.post(f"{API_BASE_URL}/documents/upload", files=files, data=data)
    response.raise_for_status()
    result = response.json()
    print(f"上传成功！")
    print(f"文档ID: {result.get('document_id')}")
    print(f"状态: {result.get('status')}")
    print(f"消息: {result.get('message')}")
except Exception as e:
    print(f"上传失败: {str(e)}")
    print(f"响应状态码: {response.status_code if 'response' in locals() else 'N/A'}")
    print(f"响应内容: {response.text if 'response' in locals() else 'N/A'}")

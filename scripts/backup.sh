#!/bin/bash
# scripts/backup.sh — 建议通过 crontab 每日执行
BACKUP_DIR="/backup/rag-$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# PostgreSQL 备份
docker exec postgres pg_dump -U raguser rag_db > "$BACKUP_DIR/pg_dump.sql"

# Milvus 数据备份 (复制 volume)
cp -r ./volumes/milvus "$BACKUP_DIR/milvus_data"

# 上传文件备份
cp -r ./data "$BACKUP_DIR/data"

# 保留最近 30 天备份
find /backup -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

echo "Backup completed: $BACKUP_DIR"

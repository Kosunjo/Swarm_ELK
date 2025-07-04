#!/bin/bash
set -e

# Elasticsearch가 시작될 때까지 대기
until curl -s http://localhost:9200 >/dev/null; do
  echo "Elasticsearch is starting..."
  sleep 5
done

# 인덱스 템플릿 또는 매핑 적용
echo "Applying mappings from backup..."
curl -X PUT "http://localhost:9200/your_index_name" -H "Content-Type: application/json" -d @/usr/share/elasticsearch/config/elasticsearch_mappings.json -u elastic:test123

echo "Elasticsearch configuration restored!"

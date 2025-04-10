FROM docker.elastic.co/elasticsearch/elasticsearch:7.10.2


#curl -X GET "http://localhost:9200/air-quality-seoul-*,stock-price-*/_mapping" -u elastic:password > elastic_mapping.json
#인덱스 정보 crowling하는 방법.
COPY elasticsearch_mappings.json /usr/share/elasticsearch/config/
COPY init-elasticsearch.sh /usr/share/elasticsearch/

CMD ["/bin/bash", "-c", "/usr/share/elasticsearch/init-elasticsearch.sh & /usr/local/bin/docker-entrypoint.sh"]

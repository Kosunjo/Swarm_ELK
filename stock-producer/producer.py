import requests
import json
import time
from kafka import KafkaProducer
from kafka.errors import KafkaError
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any
from datetime import timedelta

# --- 로깅 설정 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('stock-price-data-producer')
logger.propagate = True

logger.info("✅ Logging works now!")
# --- 환경 변수 및 설정 ---
# Kafka 설정
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'daa-kafka1:9092,daa-kafka2:9093')
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'stock-data')

# API 설정 (금융위원회_주식시세정보 조회 서비스 기준)
API_BASE_URL = os.getenv('STOCK_API_URL', 'https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService')
API_KEY = os.getenv("STOCK_API_KEY", "").strip("'").strip('"') # 기본값 제거, 필수 설정으로 간주
API_ENDPOINT = os.getenv('API_ENDPOINT', 'getStockPriceInfo')
COLLECTION_INTERVAL_SECONDS = int(os.getenv('COLLECTION_INTERVAL', '86400')) # 기본값 1시간(3600초)으로 변경 (API 특성 고려)

# --- 시총 순위 20위 주식식 종목 리스트 ---
BLUECHIP_STOCKS = {
    "005930",  # 삼성전자
    #"000660",  # SK하이닉스
    #"373220",  # LG에너지솔루션
    #"207940",  # 삼성바이오로직스
    #"005380",  # 현대차
    #"005935",  # 삼성전자우
    #"068270",  # 셀트리온
    #"000270",  # 기아
    #"012450",  # 한화에어로스페이스
    #"035420",  # NAVER
    #"105560",  # KB금융
    #"329180",  # HD현대중공업
    #"055550",  # 신한지주
    #"012330",  # 현대모비스
    #"138040",  # 메리츠금융지주
    #"005490",  # POSCO홀딩스
    #"042660",  # 한화오션
    #"028260",  # 삼성물산
    #"259960",  # 크래프톤
    #"035720",  # 카카오"""
}


# --- 데이터 가져오기 함수 ---
def get_all_stock_price_data(likeSrtnCd: str) -> list[Dict[str, Any]]:
    if not API_KEY:
        logger.error("API 키가 설정되지 않았습니다.")
        return []

    api_url = f"{API_BASE_URL}/{API_ENDPOINT}"
    one_month_ago = datetime.today() - timedelta(days=30)
    beginBasDt = one_month_ago.strftime('%Y%m%d')
    page_no = 1
    num_of_rows = 50

    collected_data = []

    while True:
        params = {
            'serviceKey': API_KEY,
            'resultType': 'json',
            'numOfRows': str(num_of_rows),
            'pageNo': str(page_no),
            'beginBasDt': beginBasDt,
            'likeSrtnCd': likeSrtnCd
        }

        try:
            logger.info(f"📄 [페이지 {page_no}] API 요청 중...")
            response = requests.get(api_url, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()

            # 데이터 존재 여부 확인
            items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
            if not items:
                logger.info(f"✅ 더 이상 데이터가 없습니다. (페이지 {page_no})")
                break

            logger.info(f"✅ 페이지 {page_no}에서 {len(items)}건 수집")
            collected_data.extend(items)

        except Exception as e:
            logger.error(f"페이지 {page_no} 수집 실패: {e}")
            break

        page_no += 1
        time.sleep(0.3)  # 과도한 요청 방지

    return collected_data
# --- Kafka 관련 함수 ---
def create_kafka_producer() -> Optional[KafkaProducer]:
    """Kafka 프로듀서 생성"""
    try:
        servers = [s.strip() for s in KAFKA_BOOTSTRAP_SERVERS.split(',')]
        producer = KafkaProducer(
            bootstrap_servers=servers,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode('utf-8'),
            acks='all',
            retries=3,
            max_in_flight_requests_per_connection=1,
            client_id='stock-price-producer-py'
        )
        logger.info(f"Kafka 프로듀서 생성 완료. Bootstrap Servers: {servers}")
        return producer
    except KafkaError as e:
        logger.error(f"Kafka 프로듀서 생성 실패: {e}")
        return None
    except Exception as e:
        logger.error(f"Kafka 프로듀서 생성 중 예상치 못한 오류: {e}")
        return None

def send_to_kafka(producer: KafkaProducer, data: Dict[str, Any]) -> bool:
    """데이터를 Kafka에 전송"""
    if not data:
        logger.warning("Kafka로 전송할 데이터가 없습니다.")
        return False

    try:
        future = producer.send(KAFKA_TOPIC, value=data)
        record_metadata = future.get(timeout=15)
        logger.info(f"메시지 전송 성공: Topic={KAFKA_TOPIC}, Partition={record_metadata.partition}, Offset={record_metadata.offset}")
        return True
    except KafkaError as e:
        logger.error(f"메시지 전송 중 Kafka 오류 발생: {e}")
        return False
    except Exception as e:
        logger.error(f"메시지 전송 중 예상치 못한 오류: {e}")
        return False

# --- 메인 실행 로직 ---
def main():

    """메인 실행 함수"""
    logger.info("="*30)
    logger.info("주식시세 데이터 Kafka 프로듀서 시작")
    logger.info(f"Kafka 서버: {KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Kafka 토픽: {KAFKA_TOPIC}")
    logger.info(f"API 엔드포인트: {API_ENDPOINT}")
    logger.info(f"데이터 수집 주기: {COLLECTION_INTERVAL_SECONDS}초")
    logger.info("="*30)

    if not API_KEY:
        logger.error("STOCK_API_KEY 환경변수가 설정되지 않아 프로듀서를 시작할 수 없습니다.")
        return

    producer = create_kafka_producer()
    if not producer:
        logger.error("Kafka 프로듀서를 초기화할 수 없습니다. 프로그램을 종료합니다.")
        return

    
    try:
        cont = 1
        for likeSrtnCd in BLUECHIP_STOCKS:
            logger.info("-" * 20)
            logger.info(f"📦 [{cont}] 종목 {likeSrtnCd} 데이터 수집 시작...")

            all_data = get_all_stock_price_data(likeSrtnCd)
            if all_data:
                for item in all_data:
                    message = {
                        "source": "stock_price_api",
                        "api_endpoint": API_ENDPOINT,
                        "search_date": datetime.today().strftime('%Y-%m-%d'),
                        "retrieved_at": datetime.now().isoformat(),
                        "symbol": likeSrtnCd,
                        "data": item
                    }
                    send_to_kafka(producer, message)
                    cont += 1
                    logger.info(f"✅ 종목 {likeSrtnCd} Kafka 전송 완료 (총 {len(all_data)}개 항목)")
            else:
                logger.info("📭 수집된 데이터가 없습니다.")

            
        logger.info(f"⏳ 다음 수집까지 {COLLECTION_INTERVAL_SECONDS}초 대기...")
        time.sleep(COLLECTION_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Ctrl+C 감지됨. 종료 절차를 시작합니다.")
    except Exception as e:
        logger.error(f"메인 루프에서 예상치 못한 오류 발생: {e}", exc_info=True)
    finally:
        if producer:
            logger.info("남아있는 메시지를 전송하고 Kafka 프로듀서를 종료합니다...")
            producer.flush(timeout=10)
            producer.close(timeout=10)
            logger.info("Kafka 프로듀서 종료 완료.")
        logger.info("프로그램 실행 종료.")

if __name__ == "__main__":
    main()
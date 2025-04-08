import requests
import json
import time
from kafka import KafkaProducer
from kafka.errors import KafkaError
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any, List
import geo_point
from geo_point import station_coordinates
# --- 로깅 설정 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s' # 함수 이름 추가
)
logger = logging.getLogger('air-quality-seoul-producer') # 로거 이름 변경

# --- 환경 변수 및 설정 ---
# Kafka 설정
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'daa-kafka1:19092,daa-kafka2:19093') # 기본값 내부 포트로 변경
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'air-quality-seoul') # 기본값 변경

# API 설정
API_BASE_URL = os.getenv('WEATHER_API_URL', 'http://apis.data.go.kr/B552584/ArpltnInforInqireSvc')
API_KEY = os.getenv('WEATHER_API_KEY') # 필수 설정
API_ENDPOINT = os.getenv('API_ENDPOINT', 'getCtprvnRltmMesureDnsty') # 기본 엔드포인트 변경
SIDO_NAME = os.getenv('SIDO_NAME', '서울') # 조회할 시도 이름
API_VERSION = os.getenv('API_VERSION', '1.3') # API 버전을 명시적으로 관리 (예시, 실제 API 문서 확인 필요)

COLLECTION_INTERVAL_SECONDS = int(os.getenv('COLLECTION_INTERVAL', '600')) # 기본값 10분

# --- 데이터 가져오기 함수 ---
def get_seoul_air_quality_data() -> Optional[List[Dict[str, Any]]]:
    """서울시 대기환경 API에서 실시간 측정소 데이터를 리스트로 가져오기"""
    if not API_KEY:
        logger.error("API 키가 설정되지 않았습니다 (WEATHER_API_KEY 환경 변수 확인).")
        return None

    api_url = f"{API_BASE_URL}/{API_ENDPOINT}"
    all_items: List[Dict[str, Any]] = [] # 모든 페이지 결과를 합칠 리스트
    

    for i in range(9):
        current_page_no = i + 1 # 페이지 번호는 1부터 시작    
        params = {
            'serviceKey': API_KEY,
            'returnType': 'json',
            'numOfRows': '100', # 한 번에 가져올 최대 row 수
            'pageNo': str(current_page_no), # 현재 페이지 번호
            'sidoName': SIDO_NAME,
            'ver': API_VERSION
        }
        # 요청 전 파라미터 로그 (API 키 제외)
        params_log = {k:v for k, v in params.items() if k != 'serviceKey'}
        logger.info(f"API 요청 시작: Page={current_page_no}/8")
        logger.debug(f"URL: {api_url}, Params: {params_log}")

        try:
            response = requests.get(api_url, params=params, timeout=25)
            response.raise_for_status() # 200 OK 아니면 예외 발생

            logger.info(f"API 응답 수신: Page={current_page_no} (Status: {response.status_code})")
            data = response.json()

            header = data.get('response', {}).get('header', {})
            result_code = header.get('resultCode')
            result_msg = header.get('resultMsg')

            # API 자체 오류 처리
            if result_code != '00':
                logger.error(f"API 응답 오류: Page={current_page_no}, Code={result_code}, Msg={result_msg}")
                # 오류 발생 시 해당 페이지만 건너뛰고 다음 페이지 시도
                continue # 다음 루프 반복으로 넘어감

            body = data.get('response', {}).get('body', {})
            items = body.get('items')

            # 해당 페이지에 아이템이 없는 경우
            if not items:
                logger.info(f"Page={current_page_no} 에서 측정 항목(items)이 없습니다.")
                # 아이템이 없어도 다음 페이지 시도
                continue # 다음 루프 반복으로 넘어감

            # 단일 객체일 경우 리스트로 변환
            if not isinstance(items, list):
                items = [items]

            logger.info(f"Page={current_page_no}: {len(items)}개의 측정소 데이터 추가.")
            all_items.extend(items) # 결과 리스트에 현재 페이지 아이템 추가

            # (선택적) API 요청 간 짧은 지연 추가
            time.sleep(0.2)

        # 루프 내에서 발생하는 예외 처리
        except requests.exceptions.RequestException as e:
            logger.error(f"API 요청 실패: Page={current_page_no}, Error={e}")
            # 오류 발생 시 해당 페이지만 건너뛰고 다음 페이지 시도
            continue # 다음 루프 반복으로 넘어감
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 오류: Page={current_page_no}, Error={e}")
            try: logger.error(f"파싱 실패 응답 내용 (일부): {response.text[:500]}")
            except: pass
            # 오류 발생 시 해당 페이지만 건너뛰고 다음 페이지 시도
            continue # 다음 루프 반복으로 넘어감
        except Exception as e:
            logger.error(f"데이터 처리 중 예상치 못한 오류: Page={current_page_no}, Error={e}", exc_info=True)
            # 오류 발생 시 해당 페이지만 건너뛰고 다음 페이지 시도
            continue # 다음 루프 반복으로 넘어감

    # for 루프가 모두 완료된 후 최종 결과 반환
    if not all_items:
        logger.warning("총 8페이지 요청 결과, 수집된 측정소 데이터가 없습니다.")
        return None
    else:
        logger.info(f"총 8페이지 요청 완료. 최종 {len(all_items)}개의 측정소 데이터 수집.")
        return all_items

# --- Kafka 관련 함수 ---
def create_kafka_producer() -> Optional[KafkaProducer]:
    """Kafka 프로듀서 생성"""
    producer = None
    retry_count = 0
    max_retries = 5
    while producer is None and retry_count < max_retries:
        try:
            retry_count += 1
            logger.info(f"Kafka 프로듀서 생성 시도 ({retry_count}/{max_retries})...")
            servers = [s.strip() for s in KAFKA_BOOTSTRAP_SERVERS.split(',')]
            producer = KafkaProducer(
                bootstrap_servers=servers,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode('utf-8'),
                acks='all',
                retries=3,
                # max_in_flight_requests_per_connection=1, # 성능에 영향 줄 수 있어 제거
                linger_ms=10, # 배치 전송 위한 짧은 대기 (ms)
                batch_size=16384, # 16KB 배치 크기
                compression_type='gzip', # 메시지 압축 사용
                client_id='air-quality-seoul-producer-py' # 클라이언트 ID 변경
            )
            logger.info(f"Kafka 프로듀서 생성 완료. Bootstrap Servers: {servers}")
            return producer
        except KafkaError as e:
            logger.error(f"Kafka 프로듀서 생성 실패 (시도 {retry_count}): {e}")
            if retry_count < max_retries:
                time.sleep(5 * retry_count) # 재시도 간 대기 시간 증가
            else:
                logger.error("최대 재시도 횟수 초과. 프로듀서 생성 실패.")
                return None
        except Exception as e:
            logger.error(f"Kafka 프로듀서 생성 중 예상치 못한 오류: {e}", exc_info=True)
            return None
    return None # 최종 실패 시

def send_station_data_to_kafka(producer: KafkaProducer, station_data: Dict[str, Any], retrieved_time: str):
    """개별 측정소 데이터를 Kafka에 전송 (메타데이터 추가)"""
    if not station_data:
        logger.warning("전송할 측정소 데이터가 없습니다.")
        return
    

    station_name = station_data.get('stationName')
    # Kafka 메시지에 공통 메타데이터 추가
    message = {
        "retrieved_at": retrieved_time, # 데이터 수집 시간
        "sidoName": SIDO_NAME,         # 시도 이름 (Logstash에서 station.province로 변경됨)
        **station_data                 # 원본 측정소 데이터 필드들을 그대로 포함
    }
    # 좌표 정보 추가
    if station_name in station_coordinates:
        message["station_location"] = station_coordinates[station_name]
    
    try:
        # 메시지 키는 측정소 이름으로 설정 (동일 측정소 데이터는 같은 파티션으로 가도록 유도)
        key = station_data.get('stationName', 'unknown-station').encode('utf-8')

        # 비동기 전송 (콜백 등록은 선택 사항)
        producer.send(KAFKA_TOPIC, key=key, value=message)
        # future = producer.send(KAFKA_TOPIC, key=key, value=message)
        # future.add_callback(on_send_success).add_errback(on_send_error) # 콜백 예시

        # 로그는 주기적으로 남기거나 콜백에서 처리 (매번 로그 남기면 성능 저하)
        # logger.debug(f"메시지 전송 요청: Topic={KAFKA_TOPIC}, Key={key.decode()}, Station={station_data.get('stationName')}")

    except KafkaError as e:
        logger.error(f"메시지 전송 중 Kafka 오류 발생 (Station: {station_data.get('stationName')}): {e}")
    except Exception as e:
        logger.error(f"메시지 전송 중 예상치 못한 오류 (Station: {station_data.get('stationName')}): {e}", exc_info=True)

# --- 메인 실행 로직 ---
def main():
    """메인 실행 함수"""
    logger.info("="*40)
    logger.info(f"{SIDO_NAME} 대기질 데이터 Kafka 프로듀서 시작")
    logger.info(f"Kafka 서버: {KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Kafka 토픽: {KAFKA_TOPIC}")
    logger.info(f"API 엔드포인트: {API_ENDPOINT}")
    logger.info(f"조회 시도: {SIDO_NAME}")
    logger.info(f"데이터 수집 주기: {COLLECTION_INTERVAL_SECONDS}초")
    logger.info("="*40)

    if not API_KEY:
        logger.error("WEATHER_API_KEY 환경변수가 설정되지 않아 프로듀서를 시작할 수 없습니다.")
        return

    producer = create_kafka_producer()
    if not producer:
        logger.error("Kafka 프로듀서를 초기화할 수 없습니다. 프로그램을 종료합니다.")
        return

    try:
        while True:
            start_time = time.time()
            logger.info("-" * 20)
            logger.info("대기질 데이터 수집 및 전송 시작...")

            retrieved_iso_time = datetime.now().isoformat() # 현재 수집 시간 기록
            station_data_list = get_seoul_air_quality_data()
            data_fetch_time = time.time() - start_time

            if station_data_list:
                logger.info(f"{len(station_data_list)}개 측정소 데이터 Kafka 전송 시작...")
                sent_count = 0
                for station_data in station_data_list:
                    send_station_data_to_kafka(producer, station_data, retrieved_iso_time)
                    sent_count +=1

                # 모든 메시지 전송 시도 후 flush (배치 전송 보장)
                producer.flush(timeout=10)
                logger.info(f"{sent_count}개 측정소 데이터 전송 완료 (소요 시간: {time.time() - start_time:.2f}초, API 요청 시간: {data_fetch_time:.2f}초)")

            else:
                logger.warning("전송할 대기질 데이터가 없습니다.")

            # 다음 실행 시간 계산 및 대기
            elapsed_time = time.time() - start_time
            wait_time = max(0, COLLECTION_INTERVAL_SECONDS - elapsed_time)
            logger.info(f"다음 작업까지 {wait_time:.1f}초 대기합니다.")
            if wait_time > 0:
                time.sleep(wait_time)

    except KeyboardInterrupt:
        logger.info("Ctrl+C 감지됨. 종료 절차를 시작합니다.")
    except Exception as e:
        logger.error(f"메인 루프에서 예상치 못한 오류 발생: {e}", exc_info=True)
    finally:
        if producer:
            logger.info("남아있는 메시지를 전송하고 Kafka 프로듀서를 종료합니다...")
            try:
                producer.flush(timeout=20) # 종료 시 타임아웃 증가
            except Exception as flush_err:
                logger.error(f"프로듀서 flush 중 오류: {flush_err}")
            try:
                producer.close(timeout=20)
            except Exception as close_err:
                 logger.error(f"프로듀서 close 중 오류: {close_err}")
            logger.info("Kafka 프로듀서 종료 완료.")
        logger.info("프로그램 실행 종료.")

if __name__ == "__main__":
    main()
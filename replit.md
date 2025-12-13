# FastAPI Demo Project

## Overview
FastAPI를 사용한 Python 웹 API 프로젝트입니다.

## Project Structure
- `main.py` - FastAPI 애플리케이션 메인 파일

## Running the Project
서버는 포트 5000에서 실행됩니다.

## API Endpoints
- `GET /` - 환영 메시지
- `GET /items` - 모든 아이템 조회
- `POST /items` - 아이템 생성 (서버가 ID 자동 생성)
- `GET /items/{item_id}` - 특정 아이템 조회
- `PUT /items/{item_id}` - 아이템 전체 교체
- `PATCH /items/{item_id}` - 아이템 부분 업데이트
- `DELETE /items/{item_id}` - 아이템 삭제
- `GET /health` - 서버 상태 확인

## API Documentation
- Swagger UI: `/docs`
- ReDoc: `/redoc`

## Recent Changes
- 2025-12-04: 초기 FastAPI 프로젝트 생성, REST 표준 준수 개선

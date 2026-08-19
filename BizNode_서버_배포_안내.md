# BizNode 서버 배포 안내

로컬 PC에서 돌던 것을 AWS로 옮기는 절차입니다. 처음부터 끝까지 **한 시간쯤** 걸립니다.

> 왜 옮기나 — 팀원이 아무 때나 API를 부를 수 있어야 하고, 매일 새벽 배치가
> 돌아야 데이터가 최신으로 유지됩니다. 개인 PC로는 둘 다 안 됩니다.

---

## 0. 준비물

```
AWS 계정            한이음 지원 계정
SSH 키페어           EC2 만들 때 발급받아 .pem 파일로 저장
로컬 데이터 덤프       infra/share/ 에 이미 있음 (124MB)
.env                DART·OpenAI·네이버 키 — 절대 저장소에 올리지 않는다
```

---

## 1. EC2 만들기

```
이름          biznode
AMI          Ubuntu Server 24.04 LTS  ★반드시 (ARM) 표시가 있는 것
인스턴스 유형   t4g.medium              2 vCPU · 4GB
키 페어        새로 생성 → biznode.pem 내려받기 (다시 못 받습니다)
스토리지       30 GiB gp3
```

**AMI를 고를 때 아키텍처가 `arm64`인지 확인하세요.** `x86_64` AMI를 t4g에 올리면
인스턴스가 안 뜹니다.

### 보안 그룹 — 여기가 제일 중요합니다

| 포트 | 소스 | 용도 |
|---|---|---|
| 22 (SSH) | **내 IP** | 접속 |
| 8100 | 0.0.0.0/0 | API — 백엔드가 부를 곳 |

**7687·5432·8001·6379는 절대 열지 마세요.** DB 포트입니다. 인터넷에 열면
기본 비밀번호를 아는 사람이 바로 들어옵니다.

팀원이 Neo4j Browser를 봐야 하면 포트를 여는 게 아니라 **SSH 터널**을 씁니다(§7).

---

## 2. 서버 기본 설정

```bash
ssh -i biznode.pem ubuntu@<서버IP>
```

```bash
# 시간대 — 안 바꾸면 크론이 UTC 로 돌아 장 마감 전에 주가를 받아옵니다
sudo timedatectl set-timezone Asia/Seoul
date                                    # KST 인지 확인

# Docker
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu
exit                                    # 그룹 적용을 위해 재접속
```

```bash
ssh -i biznode.pem ubuntu@<서버IP>
docker --version && docker compose version
```

---

## 3. 코드 올리기

```bash
git clone <저장소 주소> /srv/biznode
cd /srv/biznode
```

비공개 저장소면 SSH 키를 등록하거나, 간단히 로컬에서 밀어 넣어도 됩니다.

```bash
# 로컬 PC 에서
scp -i biznode.pem -r . ubuntu@<서버IP>:/srv/biznode
```

---

## 4. `.env` 만들기 — **저장소에 올리지 않습니다**

서버에서 직접 만듭니다.

```bash
cd /srv/biznode
cp .env.example .env
nano .env
```

```ini
# 외부 API 키 (로컬 .env 에서 복사)
DART_KEY=...
OPENAI_API_KEY=...
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...

# ★DB 비밀번호 — 기본값을 반드시 바꿉니다
NEO4J_PASSWORD=<길고 무작위인 값>
POSTGRES_PASSWORD=<길고 무작위인 값>
NEO4J_URI=bolt://neo4j:7687
POSTGRES_HOST=postgres
POSTGRES_PASSWORD 와 같은 값을 POSTGRES_DSN 에도 반영
CHROMA_HOST=chroma
CHROMA_PORT=8000
```

비밀번호는 이렇게 만들면 됩니다.

```bash
openssl rand -base64 24
```

---

## 5. 띄우기

**로컬용과 서버용 설정을 겹쳐 씁니다.**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps
```

`docker-compose.prod.yml`이 하는 일입니다.

```
DB 포트를 127.0.0.1 에만 묶는다     인터넷에서 안 보인다
비밀번호를 .env 에서 읽는다          기본값 사용을 막는다
pgweb 을 안 띄운다                  로그인이 없어 위험하다
API 를 컨테이너로 돌린다             재부팅 후 자동으로 살아난다
Neo4j 힙 1G · 페이지캐시 512M       4GB 서버에 맞춘 값
```

---

## 6. 데이터 옮기기

로컬에서 덤프를 뜨고 서버에서 복원합니다. **이미 만들어 둔 도구**가 있습니다.

```bash
# 로컬 PC 에서
bash infra/share_all.sh dump          # infra/share/ 에 3개 파일 (124MB)
scp -i biznode.pem infra/share/* ubuntu@<서버IP>:/srv/biznode/infra/share/
```

```bash
# 서버에서
cd /srv/biznode
bash infra/share_all.sh load
```

셋이 서로를 참조하므로 **반드시 셋 다** 옮겨야 합니다.

```
Neo4j        관계·노드 — 엣지가 evidence_id 로 근거를 가리킨다
PostgreSQL   기사 메타·재무·공시
ChromaDB     evidence_id 가 가리키는 근거 문장 본문
```

### 확인

```bash
curl -s localhost:8100/health
curl -s "localhost:8100/search?q=삼성&limit=3"
```

브라우저에서 `http://<서버IP>:8100/docs`와 `/preview`가 열리면 끝입니다.

---

## 7. 팀원이 DB를 봐야 할 때 — SSH 터널

포트를 여는 대신 터널을 뚫습니다.

```bash
ssh -i biznode.pem -N \
    -L 7474:localhost:7474 \
    -L 7687:localhost:7687 \
    -L 5432:localhost:5432 \
    ubuntu@<서버IP>
```

터널을 열어 둔 채로 로컬 브라우저에서 `http://localhost:7474`를 열면
**서버의 Neo4j Browser**가 나옵니다. DBeaver도 `localhost:5432`로 붙습니다.

방화벽에 구멍을 안 뚫고 되므로 **이 방법을 씁니다.**

---

## 8. 매일 자동 실행 — 크론

```bash
crontab -e
```

`batch.ops.daily`가 넷을 순서대로 부르지만, **낮에 돌 것과 밤에 돌 것이 달라서**
둘로 나눕니다.

```cron
# 낮 — PostgreSQL 만 쓰므로 서비스 중에 돌아도 된다
30 7  * * *   cd /srv/biznode && docker compose exec -T api python -m batch.ops.daily --skip-extract >> /var/log/biznode/day.log 2>&1

# 밤 — 관계 추출 · 근거 검증. Neo4j 를 고치므로 야간에만
0  2  * * *   cd /srv/biznode && docker compose exec -T api python -m batch.ops.daily --extract-limit 200 >> /var/log/biznode/night.log 2>&1
```

`--skip-extract`는 뉴스 수집·주가까지만 돌고 멈춥니다. 밤 실행은 그 둘을 한 번 더
돌게 되는데, **둘 다 멱등이라 손해가 없습니다**(같은 URL은 upsert됩니다).

`--extract-limit`는 하루 최대 추출 건수입니다. 200이면 최악이 하루 2,940원입니다.
아직 실측 전이라 **며칠 돌려 보고 조정**하세요(방법서 §11-2).

```bash
mkdir -p /var/log/biznode
```

**크론은 조용히 실패합니다.** 로그를 남기고, `/health`로 마지막 수집 시각을
확인하는 습관을 들이세요.

---

## 9. 백업

데이터가 868MB라 통째로 떠도 가볍습니다.

```cron
0 4 * * 0  cd /srv/biznode && bash infra/share_all.sh dump && \
           tar czf /home/ubuntu/backup-$(date +\%Y\%m\%d).tar.gz infra/share/
```

주 1회면 충분합니다. **가끔 로컬로 내려받아 두세요** — 서버가 통째로 날아가면
서버 안의 백업도 같이 사라집니다.

```bash
scp -i biznode.pem ubuntu@<서버IP>:/home/ubuntu/backup-*.tar.gz .
```

---

## 10. 비용 관리

```
t4g.medium   월 약 26,000원   24시간 켜 두는 값
EBS 30GB     월 약  3,500원
공인 IPv4    월 약  5,000원
             ─────────────
             월 약 34,500원
```

**AWS 예산 알림을 걸어 두세요.** 콘솔 → Billing → Budgets에서 월 40,000원으로
설정하면 초과 전에 메일이 옵니다.

인스턴스를 중지(stop)하면 EC2 요금은 안 나가지만 **EBS·IP 요금은 계속**
나갑니다. 그리고 배치가 안 돌아 데이터가 멈춥니다.

---

## 자주 막히는 곳

| 증상 | 원인 | 해결 |
|---|---|---|
| 인스턴스가 안 뜬다 | x86 AMI 를 t4g 에 올림 | arm64 AMI 로 다시 |
| API 는 뜨는데 조회가 500 | `.env` 의 DB 호스트가 `localhost` | 컨테이너 이름(`neo4j`·`postgres`)으로 |
| Neo4j 가 죽는다 | 4GB 에 힙을 크게 잡음 | `prod.yml` 의 힙 1G 유지 |
| 크론이 안 돈다 | 시간대가 UTC | `timedatectl set-timezone Asia/Seoul` |
| 밖에서 8100 접속 불가 | 보안 그룹 | 인바운드 8100 허용 확인 |
| 배치가 한글을 깨뜨린다 | 로케일 | Dockerfile 의 `PYTHONIOENCODING=utf-8` 확인 |

---

## 절대 하지 말 것

```
DB 포트(7687·5432·8001·6379)를 인터넷에 열기
기본 비밀번호(biznode_dev_pw)를 그대로 두기
.env 를 저장소에 커밋하기
pgweb 을 서버에서 띄우기 (로그인이 없습니다)
```

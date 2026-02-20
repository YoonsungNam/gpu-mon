# 사내 배포 워크플로우

> GitHub → WSL → 회사 서버 배포 흐름 상세

## 전체 흐름

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────────────┐
│  GitHub      │     │ 회사 데스크톱     │     │ 회사 서버 (K8s)           │
│  (인터넷)    │     │ WSL (Ubuntu)      │     │ (Airgap or 인트라넷)     │
│              │     │                   │     │                           │
│  gpu-mon     │────→│ git pull          │     │                           │
│  (public)    │     │                   │     │                           │
│              │     │ gpu-mon-corp      │     │                           │
│  gpu-mon-corp│────→│ git pull          │     │                           │
│  (private)   │     │                   │     │                           │
│              │     │ symlink 연결      │     │                           │
│              │     │                   │     │                           │
│              │     │ ┌───────────────┐ │     │                           │
│              │     │ │ 시나리오 A     │─┼────→│ helmfile -e corp sync    │
│              │     │ │ 직접 배포      │ │     │                           │
│              │     │ └───────────────┘ │     │                           │
│              │     │                   │     │                           │
│              │     │ ┌───────────────┐ │     │                           │
│              │     │ │ 시나리오 B     │ │     │                           │
│              │     │ │ Airgap 번들   │─┼─tar→│ ./install.sh <registry>  │
│              │     │ └───────────────┘ │     │                           │
└─────────────┘     └──────────────────┘     └──────────────────────────┘
```

---

## WSL 초기 설정 (1회)

### 1. 필수 도구 설치

```bash
sudo apt update && sudo apt install -y git curl

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install kubectl /usr/local/bin/

# helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# helmfile
wget https://github.com/helmfile/helmfile/releases/download/v0.169.2/helmfile_0.169.2_linux_amd64.tar.gz
tar xzf helmfile_*.tar.gz
sudo mv helmfile /usr/local/bin/

# helm-diff 플러그인 (helmfile diff에 필요)
helm plugin install https://github.com/databus23/helm-diff
```

### 2. kubeconfig 설정

```bash
mkdir -p ~/.kube

# K8s 관리자에게 kubeconfig 파일을 받아서 복사
cp /path/to/corp-kubeconfig ~/.kube/config

# 또는 직접 설정
kubectl config set-cluster corp-k8s \
  --server=https://<K8s-API-서버>:6443 \
  --certificate-authority=/path/to/ca.crt
kubectl config set-credentials yoonsung \
  --token=<your-token>
kubectl config set-context corp \
  --cluster=corp-k8s --user=yoonsung
kubectl config use-context corp

# 확인
kubectl get nodes
```

### 3. Repo 클론 + 심볼릭 링크

```bash
mkdir -p ~/work && cd ~/work

# 두 repo 클론
git clone https://github.com/yoonsung-samsung/gpu-mon.git
git clone <gpu-mon-corp repo URL> gpu-mon-corp

# 심볼릭 링크 설정
cd gpu-mon/environments/
ln -s ../../gpu-mon-corp/environments/corp ./corp

cd ~/work/gpu-mon/ansible/inventory/
ln -s ../../../gpu-mon-corp/ansible/inventory/corp.ini ./corp.ini

cd ~/work/gpu-mon/alerting/alertmanager/
ln -s ../../../gpu-mon-corp/alerting/alertmanager/corp.yaml ./corp.yaml

# 확인
ls -la ~/work/gpu-mon/environments/corp  # → ../../gpu-mon-corp/environments/corp 가리킴
```

---

## 시나리오 A: WSL에서 직접 Helm 배포

> 전제: WSL에서 회사 K8s API 서버에 네트워크 접근 가능

### 일상 배포 흐름

```bash
cd ~/work/gpu-mon

# 1. 최신 코드 가져오기
git checkout main && git pull
cd ../gpu-mon-corp && git pull && cd ../gpu-mon

# 2. 변경사항 확인 (dry-run)
helmfile -e corp diff

# 3. 배포
helmfile -e corp sync

# 4. 검증
kubectl -n monitoring get pods
kubectl -n clickhouse get pods
kubectl -n visualization get pods

# 5. Grafana 접속 (port-forward)
kubectl -n visualization port-forward svc/grafana 3000:3000
# 브라우저: http://localhost:3000
```

### Helm 차트 개별 배포/롤백

```bash
# 특정 릴리즈만 배포
helmfile -e corp -l name=victoriametrics sync

# 롤백
helm -n monitoring rollback victoriametrics 1
```

---

## 시나리오 B: Airgap 번들 생성 후 전달

> 전제: 회사 서버가 인터넷 완전 차단 (Airgap)

### 번들 생성 (WSL에서 실행)

```bash
cd ~/work/gpu-mon

# Docker가 실행 중인지 확인
docker info

# 번들 생성
./scripts/airgap-bundle.sh

# 결과물:
#   gpu-monitoring-airgap-YYYYMMDD-HHMMSS.tar.gz (~3-5GB)
#   
#   번들 내용물:
#   ├── images/all-images.tar.gz     # 모든 컨테이너 이미지
#   ├── charts/*.tgz                 # Helm 차트 아카이브
#   ├── deploy/                      # helmfile + 환경 설정 + 스키마 + 대시보드
#   ├── tools/                       # helm, helmfile 바이너리
#   └── install.sh                   # 설치 스크립트
```

### 번들 전달

```bash
# USB로 복사
cp gpu-monitoring-airgap-*.tar.gz /mnt/usb/

# 또는 SCP (사내 네트워크에 접근 가능한 jump 서버 경유)
scp gpu-monitoring-airgap-*.tar.gz jumphost:/tmp/
```

### 사내 서버에서 설치

```bash
# 1. 번들 압축 해제
tar xzf gpu-monitoring-airgap-*.tar.gz
cd airgap-bundle/

# 2. 설치 (사내 레지스트리 URL 인자로 전달)
./install.sh registry.internal.corp.com

# install.sh가 수행하는 작업:
#   ① docker load — 이미지 로드
#   ② docker tag + push — 사내 레지스트리에 업로드
#   ③ helm/helmfile 설치 (없으면)
#   ④ helmfile -e corp sync — K8s에 전체 스택 배포

# 3. 검증
kubectl -n monitoring get pods
kubectl -n clickhouse get pods
```

---

## ClickHouse 스키마 적용

스키마는 Helmfile 배포와 별도로 적용합니다 (DDL은 Helm 차트에 포함하지 않음).

```bash
# 직접 적용 (kubectl port-forward 사용)
kubectl -n clickhouse port-forward svc/clickhouse 9000:9000 &
cd ~/work/gpu-mon/schemas/
./apply.sh

# 또는 Airgap 번들 안에 포함되어 있으므로:
cd airgap-bundle/deploy/schemas/
./apply.sh
```

---

## 업데이트 배포

### 코드 변경 시

```bash
# 1. gpu-mon에서 최신 코드 pull
cd ~/work/gpu-mon && git pull

# 2. corp 설정 변경이 있으면 gpu-mon-corp도 pull
cd ../gpu-mon-corp && git pull && cd ../gpu-mon

# 3. 차이 확인 후 배포
helmfile -e corp diff
helmfile -e corp sync
```

### 자체 이미지 변경 시 (metadata-collector 등)

```bash
# 1. 이미지 빌드
cd ~/work/gpu-mon
./scripts/build-images.sh

# 시나리오 A: 직접 push 가능한 경우
docker tag gpu-mon/metadata-collector:dev registry.internal.corp.com/gpu-mon/metadata-collector:v0.2.0
docker push registry.internal.corp.com/gpu-mon/metadata-collector:v0.2.0

# 시나리오 B: Airgap
docker save gpu-mon/metadata-collector:dev | gzip > metadata-collector-v0.2.0.tar.gz
# 사내 서버로 전달 후:
docker load -i metadata-collector-v0.2.0.tar.gz
docker tag gpu-mon/metadata-collector:dev registry.internal.corp.com/gpu-mon/metadata-collector:v0.2.0
docker push registry.internal.corp.com/gpu-mon/metadata-collector:v0.2.0

# 3. corp values에서 이미지 태그 업데이트 후 재배포
helmfile -e corp -l name=metadata-collector sync
```

---

## 트러블슈팅

### 심볼릭 링크 확인

```bash
# 모든 심볼릭 링크가 올바른지 확인
ls -la ~/work/gpu-mon/environments/corp
# → ../../gpu-mon-corp/environments/corp

# 링크가 깨졌으면 (파일이 빨간색으로 표시되면)
# gpu-mon-corp가 올바른 위치에 있는지 확인
ls ~/work/gpu-mon-corp/environments/corp/values.yaml
```

### Helmfile 환경 확인

```bash
# corp 환경이 인식되는지 확인
cd ~/work/gpu-mon
helmfile -e corp list

# 에러: "environment corp is not defined"
# → helmfile.yaml에 corp 환경 정의 확인
# → environments/corp/values.yaml 존재 확인
```

### K8s 접근 문제

```bash
# kubeconfig 확인
kubectl config current-context  # corp 여야 함
kubectl cluster-info            # API 서버 접근 가능한지

# 네트워크 문제 시
# WSL → 회사 VPN 연결 확인
# 또는 회사 방화벽에서 WSL IP 허용 필요할 수 있음
```

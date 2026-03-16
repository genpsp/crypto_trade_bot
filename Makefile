.PHONY: help ssh-gmo-vps gmo-vps-ps gmo-vps-gmo-logs gmo-vps-dex-logs

help:
	@printf "Available targets:\n"
	@printf "  make ssh-gmo-vps       # SSH into the GMO VPS using the fixed host alias\n"
	@printf "  make gmo-vps-ps        # Show docker containers on the GMO VPS\n"
	@printf "  make gmo-vps-gmo-logs  # Tail recent GMO bot logs on the GMO VPS\n"
	@printf "  make gmo-vps-dex-logs  # Tail recent DEX bot logs on the GMO VPS\n"

ssh-gmo-vps:
	ssh gmo-vps

gmo-vps-ps:
	ssh gmo-vps "docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'"

gmo-vps-gmo-logs:
	ssh gmo-vps "docker logs --since 30m crypto-trade-gmo-bot-bot-1 2>&1 | tail -n 200"

gmo-vps-dex-logs:
	ssh gmo-vps "docker logs --since 30m crypto_trade_bot-bot-1 2>&1 | tail -n 120"

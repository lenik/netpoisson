# bash completion for netpoisson

_netpoisson()
{
	local cur prev words cword
	_init_completion || return

	case $prev in
	-H|--host|--ssh)
		_known_hosts_real -- "$cur"
		return
		;;
	-p|--port|-l|--lambda|-s|--stats|--payload|--connections|--max-pending|--status-interval|--bucket|--window|--seed|--report|--timeout|--ssh-command|--remote-command|--ssh-option|--profile|--web)
		return
		;;
	--profile)
		COMPREPLY=($(compgen -W 'ssh-interactive ssh-bulk' -- "$cur"))
		return
		;;
	esac

	if [[ $cur == -* ]]; then
		COMPREPLY=($(compgen -W '
			-d --daemon
			--stdio-server
			--ssh --ssh-command --remote-command --ssh-option
			--install --remove
			-H --host
			-p --port
			-u --udp
			--tls --psk --cert --key --tls-ca --tls-insecure
			-l --lambda
			--payload --connections --max-pending
			--status-interval --bucket --window --seed --profile --timeout
			-s --stats
			-w --web --no-open --json --report
			-v --verbose
			-q --quiet
			-h --help
			-V --version
		' -- "$cur"))
		return
	fi

	_known_hosts_real -- "$cur"
}

complete -F _netpoisson netpoisson

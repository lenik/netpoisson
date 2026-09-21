# bash completion for netpoisson

_netpoisson()
{
	local cur prev words cword
	_init_completion || return

	case $prev in
	-H|--host|--web-host)
		_known_hosts_real -- "$cur"
		return
		;;
	-p|--port|--web-port|-l|--lambda|-s|--stats)
		return
		;;
	esac

	if [[ $cur == -* ]]; then
		COMPREPLY=($(compgen -W '
			-d --daemon
			-H --host
			-p --port
			-u --udp
			-l --lambda
			-s --stats
			-w --web
			--web-host --web-port
			-v --verbose
			-q --quiet
			-h --help
			--version
		' -- "$cur"))
		return
	fi

	_known_hosts_real -- "$cur"
}

complete -F _netpoisson netpoisson

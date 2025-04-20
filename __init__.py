from pyplanet.apps.config import AppConfig # type: ignore
from pyplanet.apps.core.maniaplanet import callbacks as mp_signals # type: ignore
from pyplanet.apps.core.trackmania import callbacks as tm_signals # type: ignore
from pyplanet.contrib.command import Command # type: ignore
from enum import IntEnum
import logging

logger = logging.getLogger(__name__)

class State(IntEnum):
	STOPPED = 0
	STARTING = 1
	TIME_ATTACK = 2
	TEAMS = 3

class TMITApp(AppConfig):
	"""
	App to manage community stuff for TrackMania Italia.
	"""
	# todo, maybe we can use GetServerPassword gbx call to call nadeo online services and download maps from there?
	game_dependencies = ['trackmania_next']
	app_dependencies = ['core.maniaplanet', 'core.trackmania']
	NAMESPACE = 'tmit'
	TA_MESSAGE = "$0f0$iIncomincia la fase di Time Attack. GLHF."
	TEAMS_MESSAGE = "$0f0$iIncomincia la gara a squadre. GLHF."
	TIME_ATTACK_MODE = 'Trackmania/TM_TimeAttack_Online.Script.txt'
	# TEAMS_MODE = 'Trackmania/TM_Teams_Online.Script.txt'
	TEAMS_MODE = 'Modes/Trackmania/TM_Teams_Online_WinBonus.Script.txt'
	TA_BASE_TIME = 60   # base seconds to play on a TA map
	TA_TIME_DIVIDER = 5  # additional seconds: AT / divider * 60

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.players = []
		self.blue = []
		self.red = []
		self.state = State.STOPPED

	async def on_start(self):
		await super().on_start()
		self.init()
		# Commands and permissions
		await self.instance.permission_manager.register('start', 'Start TA + teams gamemode sequence', app=self, min_level=1)
		await self.instance.permission_manager.register('balance', 'Balance teams', app=self, min_level=1)
		await self.instance.permission_manager.register('info', 'Returns information for debugging purposes', app=self, min_level=1)
		await self.instance.permission_manager.register('end', 'Ends the gamemode equence', app=self, min_level=1)
		await self.instance.command_manager.register(
			Command(command='start', namespace=[self.NAMESPACE], aliases=['s'], target=self.start, perms='tmit:start', admin=True,
		   			description="Starts Time Attack mode, followed by Teams mode with balanced teams based on TA results"),
					   # todo add arguments for 
			Command(command='balance', namespace=[self.NAMESPACE], aliases=['b'], target=self.balance, perms='tmit:balance', admin=True,
		   			description="Balances the teams based on previous TA results (useful if a player has to leave and teams need rebalancing)"),
			Command(command='info', namespace=[self.NAMESPACE], aliases=['i'], target=self.info, perms='tmit:teams', admin=True,
		   			description="Print app info for debug purposes"),
			Command(command='end', namespace=[self.NAMESPACE], aliases=['e'], target=self.end, perms='tmit:end', admin=True,
		   			description="Ends the gameplay sequence"),
		)
		# Register signals
		self.context.signal.listen(mp_signals.player.player_enter_player_slot, self.player_enter_player_slot)
		self.context.signals.listen(mp_signals.flow.match_start, self.match_start)
		self.context.signals.listen(tm_signals.scores, self.scores)

	async def start(self, player, data, **kwargs):
		logger.debug("Match manager started: Beginning Time Attack phase.")
		self.init()
		await self.instance.mode_manager.set_next_script(self.TIME_ATTACK_MODE)
		await self.instance.gbx('RestartMap')
		self.state = State.STARTING

	async def balance(self, player, data, **kwargs):
		if self.state != State.TEAMS:
			await self.instance.chat(f"Match needs to be in teams mode in order to execute this command (current status: {self.state.name}).", player)
			return
		self.balance_teams()
		gbx_calls = []
		for player in self.blue:
			gbx_calls.append(self.instance.gbx('ForcePlayerTeam', player['login'], 0))
		for player in self.red:
			gbx_calls.append(self.instance.gbx('ForcePlayerTeam', player['login'], 1))
		await self.instance.gbx.multicall(*gbx_calls)
		logger.debug("Teams rebalancing performed.")

	async def info(self, player, data, **kwargs):
		if self.state != State.TEAMS:
			await self.instance.chat(f"Plugin status: {self.state}.", player)
			return
		message = "Players: "
		for i, player in enumerate(self.players):
			if player['login'] in self.blue:
				color = "$00f"
			elif player['login'] in self.red:
				color = "f00"
			else:
				color = ""
			message += f"{i:2d} {color}{player['nickname']}$z ({player['time'] / 1000}) "
		await self.instance.chat(message)

	async def end(self, player, **kwargs):
		self.state = State.STOPPED
		await self.instance.chat("Match aborted.", player)
		logger.debug("Stopped match manager.")

	async def match_start(self, **kwargs):
		if self.state == State.STARTING:
			self.state = State.TIME_ATTACK
			await self.set_ta_duration()
			await self.instance.chat(self.TA_MESSAGE)
			logger.debug(f"Match start callback: Setting state to {self.state.name}")

	async def player_enter_player_slot(self, player, **kwargs):
		if self.state != State.TEAMS:
			return
		if player.login in self.blue:
			team = 0
		elif player.login in self.blue:
			team = 1
		else:
			# todo should we force spectator here?
			logger.debug(f"Player {player.nickname} wasn't found in teams list.")
			return
		await self.instance.gbx('ForcePlayerTeam', player.login, team)
		logger.debug(f"Player {player.nickname} assigned to team {team}")

	async def scores(self, players, section, **kwargs):
		if section != 'EndMap':  # avoid multiple executions
			return
		if self.state == State.TEAMS:
			# todo send a message here on end of teams match maybe?
			await self.instance.mode_manager.set_next_script(self.TIME_ATTACK_MODE)
			self.state = State.STOPPED
		if self.state != State.TIME_ATTACK:
			return
		self.players = [dict(login=player['player'].login, 
					   		nickname=player['player'].nickname, 
							time=player['best_race_time']) for player in players]
		self.players.sort(key=lambda player: player['time'])
		await self.instance.mode_manager.set_next_script(self.TEAMS_MODE)
		self.balance_teams()  # todo does this work?
		self.state = State.TEAMS
		logger.debug(f"Scores callback: Stored player times and move to {self.state.name}.")

	async def set_ta_duration(self):
		at_seconds = self.instance.map_manager.current_map.time_author / 1000
		ta_duration = self.TA_BASE_TIME + int(at_seconds / self.TA_TIME_DIVIDER) * 60
		await self.instance.mode_manager.update_settings({
			"S_TimeLimit": ta_duration
		})
		logger.debug(f"Set map duration to {ta_duration} seconds.")

	def init(self):
		self.players = []
		self.blue = []
		self.red = []
		self.state = State.STOPPED

	def balance_teams(self):
		"""
		Create two teams based on the sorted scores in the players list. Teams are assigned in "snake" order:
		B	|	R
		1	|	2
		4	|	3
		5	|	6
		8	|	7
		etc.
		"""
		self.blue = []
		self.red = []
		playing = set(player.login for player in self.instance.player_manager.online if not player.flow.is_spectator)
		playing_sorted = [player for player in self.players if player['login'] in playing]
		for i, player in enumerate(playing_sorted):
			i %= 4
			if i == 0 or i == 3:
				self.blue.append(player['login'])
			else:
				self.red.append(player['login'])

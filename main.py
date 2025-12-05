import pygame
import socket
import threading
import pickle
import math
import time
import random
import sys
from collections import deque

# ==================================================================================
# КОНФИГУРАЦИЯ И КОНСТАНТЫ
# ==================================================================================

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
FPS = 60
SERVER_IP = '127.0.0.1'
SERVER_PORT = 5555
BUFFER_SIZE = 8192

# Цвета
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (200, 50, 50)         # Dire / Enemy
GREEN = (50, 200, 50)       # Radiant / Ally
BLUE = (50, 50, 200)        # Player
YELLOW = (200, 200, 50)     # Experience/Gold
GRAY = (100, 100, 100)
DARK_GRAY = (40, 40, 40)
CYAN = (0, 255, 255)        # Mana

# Параметры карты
MAP_WIDTH = 2000
MAP_HEIGHT = 2000

# Типы сущностей
TYPE_HERO = 1
TYPE_CREEP = 2
TYPE_TOWER = 3
TYPE_NEXUS = 4
TYPE_PROJECTILE = 5

# Команды
TEAM_RADIANT = 0
TEAM_DIRE = 1

# ==================================================================================
# ВСПОМОГАТЕЛЬНЫЕ КЛАССЫ (MATH & UTILS)
# ==================================================================================

class Vector2:
    """Упрощенный класс вектора для совместимости и сериализации"""
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)

    def to_tuple(self):
        return (int(self.x), int(self.y))

    def distance_to(self, other):
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)

    def normalize(self):
        mag = math.sqrt(self.x**2 + self.y**2)
        if mag != 0:
            self.x /= mag
            self.y /= mag
        return self

    def __add__(self, other):
        return Vector2(self.x + other.x, self.y + other.y)

    def __sub__(self, other):
        return Vector2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar):
        return Vector2(self.x * scalar, self.y * scalar)

    def copy(self):
        return Vector2(self.x, self.y)

def check_collision(entity_a, entity_b):
    dist = entity_a.pos.distance_to(entity_b.pos)
    return dist < (entity_a.radius + entity_b.radius)

# ==================================================================================
# ИГРОВЫЕ СУЩНОСТИ (SHARED LOGIC)
# ==================================================================================

class Entity:
    def __init__(self, uid, x, y, radius, team):
        self.uid = uid
        self.pos = Vector2(x, y)
        self.radius = radius
        self.team = team
        self.hp = 100
        self.max_hp = 100
        self.alive = True
        self.type = 0
        self.to_delete = False

    def take_damage(self, amount):
        self.hp -= amount
        if self.hp <= 0:
            self.hp = 0
            self.alive = False
            self.on_death()

    def on_death(self):
        pass # Переопределяется

    def update(self, dt):
        pass

class Hero(Entity):
    def __init__(self, uid, x, y, team, name="Hero"):
        super().__init__(uid, x, y, 25, team)
        self.type = TYPE_HERO
        self.name = name
        self.max_hp = 500
        self.hp = 500
        self.mana = 200
        self.max_mana = 200
        self.speed = 180
        self.attack_range = 150
        self.attack_cooldown = 0
        self.attack_speed = 1.0 # секунд на атаку
        self.target_pos = Vector2(x, y)
        self.level = 1
        self.exp = 0
        self.gold = 0
        
        # Способности (Cooldowns)
        self.q_cd = 0
        self.w_cd = 0
        self.e_cd = 0
        self.q_max_cd = 5
        self.w_max_cd = 10
        self.e_max_cd = 2

    def update(self, dt):
        if not self.alive: return

        # Движение
        if self.pos.distance_to(self.target_pos) > 5:
            direction = (self.target_pos - self.pos).normalize()
            self.pos += direction * self.speed * dt

        # Кулдауны
        if self.attack_cooldown > 0: self.attack_cooldown -= dt
        if self.q_cd > 0: self.q_cd -= dt
        if self.w_cd > 0: self.w_cd -= dt
        if self.e_cd > 0: self.e_cd -= dt

        # Реген
        if self.hp < self.max_hp: self.hp += 1 * dt
        if self.mana < self.max_mana: self.mana += 2 * dt

    def on_death(self):
        self.pos = Vector2(-1000, -1000) # Убрать с карты
        # Логика респавна обрабатывается сервером

class Creep(Entity):
    def __init__(self, uid, x, y, team):
        super().__init__(uid, x, y, 15, team)
        self.type = TYPE_CREEP
        self.max_hp = 150
        self.hp = 150
        self.speed = 100
        self.attack_range = 50
        self.damage = 10
        self.attack_cooldown = 0
        self.target_id = None
        
        # Waypoints (упрощенно: идти к вражеской базе)
        if team == TEAM_RADIANT:
            self.waypoints = [Vector2(MAP_WIDTH - 200, MAP_HEIGHT - 200)]
        else:
            self.waypoints = [Vector2(200, 200)]

    def update(self, dt):
        if not self.alive: return
        
        # Простое движение к точке
        if self.waypoints:
            target = self.waypoints[0]
            if self.pos.distance_to(target) > 5:
                direction = (target - self.pos).normalize()
                self.pos += direction * self.speed * dt
            else:
                self.waypoints.pop(0)

        if self.attack_cooldown > 0:
            self.attack_cooldown -= dt

class Tower(Entity):
    def __init__(self, uid, x, y, team):
        super().__init__(uid, x, y, 40, team)
        self.type = TYPE_TOWER
        self.max_hp = 1000
        self.hp = 1000
        self.attack_range = 400
        self.damage = 50
        self.attack_cooldown = 0
        self.attack_speed = 1.5

    def update(self, dt):
        if not self.alive: return
        if self.attack_cooldown > 0:
            self.attack_cooldown -= dt

class Nexus(Entity):
    def __init__(self, uid, x, y, team):
        super().__init__(uid, x, y, 60, team)
        self.type = TYPE_NEXUS
        self.max_hp = 2500
        self.hp = 2500

class Projectile(Entity):
    def __init__(self, uid, x, y, owner_id, target_pos, speed, damage, p_type="auto"):
        super().__init__(uid, x, y, 5, -1) # Team -1 means neutral/technical
        self.type = TYPE_PROJECTILE
        self.owner_id = owner_id
        self.target_pos = target_pos
        self.speed = speed
        self.damage = damage
        self.p_type = p_type # 'auto', 'skill_q'
        self.duration = 2.0 # Секунд жизни

    def update(self, dt):
        direction = (self.target_pos - self.pos).normalize()
        self.pos += direction * self.speed * dt
        self.duration -= dt
        if self.duration <= 0 or self.pos.distance_to(self.target_pos) < 5:
            self.to_delete = True

# ==================================================================================
# СЕРВЕРНАЯ ЧАСТЬ
# ==================================================================================

class GameServer:
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind((SERVER_IP, SERVER_PORT))
        self.socket.listen(5)
        self.clients = {} # {addr: conn}
        self.players = {} # {addr: hero_uid}
        
        self.entities = {}
        self.next_uid = 1000
        
        self.running = True
        self.lock = threading.Lock()
        
        # Инициализация карты
        self.init_map()
        
        print(f"[SERVER] Started on {SERVER_IP}:{SERVER_PORT}")

    def get_uid(self):
        with self.lock:
            self.next_uid += 1
            return self.next_uid

    def init_map(self):
        # Radiant
        self.spawn_entity(Nexus(1, 200, 200, TEAM_RADIANT))
        self.spawn_entity(Tower(2, 600, 600, TEAM_RADIANT))
        self.spawn_entity(Tower(3, 900, 900, TEAM_RADIANT))
        
        # Dire
        self.spawn_entity(Nexus(10, MAP_WIDTH-200, MAP_HEIGHT-200, TEAM_DIRE))
        self.spawn_entity(Tower(11, MAP_WIDTH-600, MAP_HEIGHT-600, TEAM_DIRE))
        self.spawn_entity(Tower(12, MAP_WIDTH-900, MAP_HEIGHT-900, TEAM_DIRE))
        
        self.last_creep_spawn = time.time()
        self.respawn_timers = {} # {hero_uid: time_left}

    def spawn_entity(self, entity):
        self.entities[entity.uid] = entity

    def handle_client(self, conn, addr):
        print(f"[SERVER] New connection: {addr}")
        
        # Создаем героя для игрока
        team = TEAM_RADIANT if len(self.players) % 2 == 0 else TEAM_DIRE
        spawn_pos = (250, 250) if team == TEAM_RADIANT else (MAP_WIDTH-250, MAP_HEIGHT-250)
        
        hero_uid = self.get_uid()
        hero = Hero(hero_uid, spawn_pos[0], spawn_pos[1], team, f"Player {len(self.players)+1}")
        
        with self.lock:
            self.spawn_entity(hero)
            self.players[addr] = hero_uid
            
        # Отправляем ID игроку
        try:
            conn.send(pickle.dumps({"type": "INIT", "uid": hero_uid, "map_size": (MAP_WIDTH, MAP_HEIGHT)}))
        except:
            return

        while self.running:
            try:
                data = conn.recv(BUFFER_SIZE)
                if not data: break
                
                input_data = pickle.loads(data)
                self.process_input(hero_uid, input_data)
                
            except Exception as e:
                print(f"[SERVER] Error with {addr}: {e}")
                break
                
        # Disconnect
        print(f"[SERVER] Disconnected {addr}")
        with self.lock:
            if hero_uid in self.entities:
                del self.entities[hero_uid]
            del self.clients[addr]
            del self.players[addr]
        conn.close()

    def process_input(self, hero_uid, data):
        with self.lock:
            if hero_uid not in self.entities: return
            hero = self.entities[hero_uid]
            if not hero.alive: return
            
            if data['type'] == 'MOVE':
                hero.target_pos = Vector2(data['x'], data['y'])
                
            elif data['type'] == 'ATTACK':
                # Простая атака по площади (для упрощения)
                pass # Реализуем в update_game
                
            elif data['type'] == 'SKILL_Q':
                if hero.q_cd <= 0 and hero.mana >= 20:
                    hero.q_cd = hero.q_max_cd
                    hero.mana -= 20
                    # Fireball
                    target = Vector2(data['x'], data['y'])
                    proj = Projectile(self.get_uid(), hero.pos.x, hero.pos.y, hero.uid, target, 400, 50, "skill_q")
                    self.spawn_entity(proj)

    def game_loop(self):
        clock = pygame.time.Clock()
        
        while self.running:
            dt = clock.tick(60) / 1000.0
            
            with self.lock:
                # Спавн крипов
                if time.time() - self.last_creep_spawn > 30: # Каждые 30 сек
                    self.last_creep_spawn = time.time()
                    for i in range(3):
                        c1 = Creep(self.get_uid(), 300 + i*20, 300, TEAM_RADIANT)
                        c2 = Creep(self.get_uid(), MAP_WIDTH-300 - i*20, MAP_HEIGHT-300, TEAM_DIRE)
                        self.spawn_entity(c1)
                        self.spawn_entity(c2)
                
                # Обновление сущностей
                to_remove = []
                entities_list = list(self.entities.values())
                
                for uid, ent in self.entities.items():
                    ent.update(dt)
                    if not ent.alive and ent.type != TYPE_HERO:
                        to_remove.append(uid)
                    if getattr(ent, 'to_delete', False):
                        to_remove.append(uid)
                        
                    # Логика боя (простая)
                    if ent.type in [TYPE_HERO, TYPE_CREEP, TYPE_TOWER] and ent.alive:
                        if hasattr(ent, 'attack_cooldown') and ent.attack_cooldown <= 0:
                            # Ищем цель
                            closest = None
                            min_dist = ent.attack_range
                            
                            for other in entities_list:
                                if other.uid != uid and other.team != ent.team and other.alive and other.type != TYPE_PROJECTILE:
                                    dist = ent.pos.distance_to(other.pos)
                                    if dist <= min_dist:
                                        min_dist = dist
                                        closest = other
                            
                            if closest:
                                ent.attack_cooldown = getattr(ent, 'attack_speed', 1.0)
                                # Создаем снаряд автоатаки
                                p = Projectile(self.get_uid(), ent.pos.x, ent.pos.y, uid, closest.pos, 300, getattr(ent, 'damage', 20))
                                self.spawn_entity(p)
                                
                    # Коллизии снарядов
                    if ent.type == TYPE_PROJECTILE:
                         for other in entities_list:
                             if other.uid != ent.owner_id and other.type != TYPE_PROJECTILE and other.alive:
                                 # Проверка: снаряд не должен бить своих, если это не лечение (тут только урон)
                                 # Но нам надо знать команду владельца. Упростим: берем владельца из entities
                                 owner = self.entities.get(ent.owner_id)
                                 if owner and owner.team != other.team:
                                     if check_collision(ent, other):
                                         other.take_damage(ent.damage)
                                         ent.to_delete = True
                                         break

                # Обработка смерти героев (Респавн)
                for uid, ent in self.entities.items():
                    if ent.type == TYPE_HERO and not ent.alive:
                        if uid not in self.respawn_timers:
                            self.respawn_timers[uid] = 5 # 5 сек респавн
                            print(f"[GAME] Hero {uid} died. Respawn in 5s.")
                
                # Таймеры респавна
                uids_to_respawn = []
                for uid in list(self.respawn_timers.keys()):
                    self.respawn_timers[uid] -= dt
                    if self.respawn_timers[uid] <= 0:
                        uids_to_respawn.append(uid)
                
                for uid in uids_to_respawn:
                    del self.respawn_timers[uid]
                    if uid in self.entities:
                        hero = self.entities[uid]
                        hero.alive = True
                        hero.hp = hero.max_hp
                        hero.mana = hero.max_mana
                        if hero.team == TEAM_RADIANT:
                            hero.pos = Vector2(250, 250)
                        else:
                            hero.pos = Vector2(MAP_WIDTH-250, MAP_HEIGHT-250)
                        hero.target_pos = hero.pos.copy()

                for uid in to_remove:
                    if uid in self.entities:
                        del self.entities[uid]

                # Бродкаст состояния
                game_state = {
                    "entities": self.entities,
                    "time": time.time()
                }
                data = pickle.dumps(game_state)
                
            # Отправка всем (вне лока, чтобы не блокировать логику)
            bad_clients = []
            for addr, conn in self.clients.items():
                try:
                    conn.send(data)
                except:
                    bad_clients.append(addr)
            
            for addr in bad_clients:
                del self.clients[addr]

    def start(self):
        threading.Thread(target=self.game_loop, daemon=True).start()
        while self.running:
            conn, addr = self.socket.accept()
            self.clients[addr] = conn
            threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

# ==================================================================================
# КЛИЕНТСКАЯ ЧАСТЬ
# ==================================================================================

class GameClient:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("Local MOBA Prototype")
        self.font = pygame.font.SysFont("Arial", 14)
        self.large_font = pygame.font.SysFont("Arial", 24)
        
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connected = False
        self.my_uid = -1
        self.entities = {}
        
        # Камера
        self.camera_x = 0
        self.camera_y = 0
        self.map_size = (MAP_WIDTH, MAP_HEIGHT)

    def connect(self, ip):
        try:
            self.socket.connect((ip, SERVER_PORT))
            
            # Получаем первый пакет инициализации
            data = self.socket.recv(BUFFER_SIZE)
            init_packet = pickle.loads(data)
            
            if init_packet['type'] == "INIT":
                self.my_uid = init_packet['uid']
                self.map_size = init_packet['map_size']
                print(f"[CLIENT] Connected! My UID: {self.my_uid}")
                self.connected = True
                
                # Запуск потока приема данных
                threading.Thread(target=self.receive_loop, daemon=True).start()
                self.run()
        except Exception as e:
            print(f"[CLIENT] Connection failed: {e}")

    def receive_loop(self):
        while self.connected:
            try:
                # В TCP поток может склеиваться, здесь упрощенная реализация
                # Для продакшена нужно читать заголовок длины сообщения
                data = self.socket.recv(BUFFER_SIZE * 4) 
                if not data: break
                
                try:
                    state = pickle.loads(data)
                    self.entities = state['entities']
                except:
                    pass # Игнорируем битые пакеты (бывает при склейке)
                    
            except Exception as e:
                print(f"[CLIENT] Recv error: {e}")
                self.connected = False
                break

    def handle_input(self):
        keys = pygame.key.get_pressed()
        mouse_buttons = pygame.mouse.get_pressed()
        mouse_pos = pygame.mouse.get_pos()
        
        # Конвертация координат экрана в координаты мира
        world_x = mouse_pos[0] + self.camera_x
        world_y = mouse_pos[1] + self.camera_y
        
        # Движение камеры стрелками
        if keys[pygame.K_RIGHT]: self.camera_x += 10
        if keys[pygame.K_LEFT]: self.camera_x -= 10
        if keys[pygame.K_DOWN]: self.camera_y += 10
        if keys[pygame.K_UP]: self.camera_y -= 10
        
        # Ограничение камеры
        self.camera_x = max(0, min(self.camera_x, self.map_size[0] - WINDOW_WIDTH))
        self.camera_y = max(0, min(self.camera_y, self.map_size[1] - WINDOW_HEIGHT))

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.connected = False
                pygame.quit()
                sys.exit()
                
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 3: # Правая кнопка - Движение
                    self.send_command({'type': 'MOVE', 'x': world_x, 'y': world_y})
                if event.button == 1: # Левая кнопка - Атака/Выбор (упрощенно как Move пока)
                    pass 

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_q:
                    self.send_command({'type': 'SKILL_Q', 'x': world_x, 'y': world_y})

    def send_command(self, data):
        if self.connected:
            try:
                self.socket.send(pickle.dumps(data))
            except:
                pass

    def draw_health_bar(self, entity, screen_x, screen_y):
        width = 50
        height = 6
        hp_pct = entity.hp / entity.max_hp
        
        pygame.draw.rect(self.screen, BLACK, (screen_x - width//2, screen_y - entity.radius - 10, width, height))
        color = GREEN if entity.team == TEAM_RADIANT else RED
        pygame.draw.rect(self.screen, color, (screen_x - width//2, screen_y - entity.radius - 10, width * hp_pct, height))

    def draw(self):
        self.screen.fill(DARK_GRAY)
        
        # Рисуем сетку земли
        grid_size = 100
        start_x = self.camera_x // grid_size * grid_size
        start_y = self.camera_y // grid_size * grid_size
        
        for x in range(start_x - grid_size, start_x + WINDOW_WIDTH + grid_size, grid_size):
            pygame.draw.line(self.screen, (50, 50, 50), (x - self.camera_x, 0), (x - self.camera_x, WINDOW_HEIGHT))
        for y in range(start_y - grid_size, start_y + WINDOW_HEIGHT + grid_size, grid_size):
            pygame.draw.line(self.screen, (50, 50, 50), (0, y - self.camera_y), (WINDOW_WIDTH, y - self.camera_y))

        # Отрисовка сущностей
        # Сортируем по Y для псевдо-глубины
        draw_list = sorted(self.entities.values(), key=lambda e: e.pos.y)
        
        my_hero = None
        
        for ent in draw_list:
            sx = int(ent.pos.x - self.camera_x)
            sy = int(ent.pos.y - self.camera_y)
            
            # Пропускаем, если вне экрана
            if sx < -50 or sx > WINDOW_WIDTH + 50 or sy < -50 or sy > WINDOW_HEIGHT + 50:
                continue
                
            if ent.uid == self.my_uid:
                my_hero = ent
                # Подсветка себя
                pygame.draw.circle(self.screen, WHITE, (sx, sy), ent.radius + 2, 1)

            color = GRAY
            if ent.type == TYPE_HERO:
                color = BLUE if ent.team == TEAM_RADIANT else (200, 100, 100)
            elif ent.type == TYPE_CREEP:
                color = GREEN if ent.team == TEAM_RADIANT else RED
            elif ent.type == TYPE_TOWER:
                color = (150, 255, 150) if ent.team == TEAM_RADIANT else (255, 150, 150)
            elif ent.type == TYPE_NEXUS:
                color = YELLOW
            elif ent.type == TYPE_PROJECTILE:
                color = CYAN

            pygame.draw.circle(self.screen, color, (sx, sy), ent.radius)
            
            # HP Bar
            if ent.type != TYPE_PROJECTILE:
                self.draw_health_bar(ent, sx, sy)
                
            # Имя над героем
            if ent.type == TYPE_HERO:
                name_surf = self.font.render(ent.name, True, WHITE)
                self.screen.blit(name_surf, (sx - name_surf.get_width()//2, sy - ent.radius - 25))

        # UI
        self.draw_ui(my_hero)
        self.draw_minimap()

        pygame.display.flip()

    def draw_ui(self, hero):
        # Панель скиллов
        ui_y = WINDOW_HEIGHT - 80
        pygame.draw.rect(self.screen, (20, 20, 20), (0, ui_y, WINDOW_WIDTH, 80))
        
        if hero:
            # HP / Mana text
            stats = f"HP: {int(hero.hp)}/{hero.max_hp}  MANA: {int(hero.mana)}/{hero.max_mana}"
            txt = self.large_font.render(stats, True, WHITE)
            self.screen.blit(txt, (250, ui_y + 25))
            
            # Скиллы
            skills = [("Q", hero.q_cd, hero.q_max_cd), ("W", hero.w_cd, hero.w_max_cd), ("E", hero.e_cd, hero.e_max_cd)]
            for i, (key, cd, max_cd) in enumerate(skills):
                box_x = 500 + i * 70
                box_y = ui_y + 10
                
                # Фон скилла
                color = (100, 100, 200) if cd <= 0 else (50, 50, 50)
                pygame.draw.rect(self.screen, color, (box_x, box_y, 60, 60))
                pygame.draw.rect(self.screen, WHITE, (box_x, box_y, 60, 60), 2)
                
                # Текст кнопки
                key_txt = self.large_font.render(key, True, WHITE)
                self.screen.blit(key_txt, (box_x + 20, box_y + 5))
                
                # Кулдаун (затемнение)
                if cd > 0:
                    ratio = cd / max_cd
                    h = int(60 * ratio)
                    s = pygame.Surface((60, h), pygame.SRCALPHA)
                    s.fill((0, 0, 0, 180))
                    self.screen.blit(s, (box_x, box_y + (60 - h)))
                    
                    cd_txt = self.font.render(f"{cd:.1f}", True, WHITE)
                    self.screen.blit(cd_txt, (box_x + 15, box_y + 35))

    def draw_minimap(self):
        map_size = 150
        margin = 20
        x = margin
        y = WINDOW_HEIGHT - map_size - margin
        
        # Фон
        pygame.draw.rect(self.screen, BLACK, (x, y, map_size, map_size))
        pygame.draw.rect(self.screen, WHITE, (x, y, map_size, map_size), 2)
        
        scale_x = map_size / self.map_size[0]
        scale_y = map_size / self.map_size[1]
        
        for ent in self.entities.values():
            mx = x + int(ent.pos.x * scale_x)
            my = y + int(ent.pos.y * scale_y)
            
            c = GRAY
            if ent.team == TEAM_RADIANT: c = GREEN
            elif ent.team == TEAM_DIRE: c = RED
            
            size = 2
            if ent.type == TYPE_HERO:
                size = 4
                c = WHITE if ent.uid == self.my_uid else c
            elif ent.type == TYPE_TOWER:
                size = 3
            
            pygame.draw.circle(self.screen, c, (mx, my), size)
            
        # Рамка камеры
        cx = x + int(self.camera_x * scale_x)
        cy = y + int(self.camera_y * scale_y)
        cw = int(WINDOW_WIDTH * scale_x)
        ch = int(WINDOW_HEIGHT * scale_y)
        pygame.draw.rect(self.screen, WHITE, (cx, cy, cw, ch), 1)

    def run(self):
        clock = pygame.time.Clock()
        while self.connected:
            self.handle_input()
            self.draw()
            clock.tick(FPS)
            
            # Центрирование камеры на герое
            if self.my_uid in self.entities:
                me = self.entities[self.my_uid]
                self.camera_x = me.pos.x - WINDOW_WIDTH // 2
                self.camera_y = me.pos.y - WINDOW_HEIGHT // 2

# ==================================================================================
# ГЛАВНОЕ МЕНЮ И ЗАПУСК
# ==================================================================================

def main_menu():
    pygame.init()
    screen = pygame.display.set_mode((600, 400))
    pygame.display.set_caption("MOBA Launcher")
    font = pygame.font.SysFont("Arial", 30)
    
    input_box = pygame.Rect(200, 200, 200, 50)
    color_inactive = pygame.Color('lightskyblue3')
    color_active = pygame.Color('dodgerblue2')
    color = color_inactive
    active = False
    text = '127.0.0.1'
    
    while True:
        screen.fill((30, 30, 30))
        
        title = font.render("Simple Python MOBA", True, WHITE)
        screen.blit(title, (180, 50))
        
        # Кнопки
        btn_host = pygame.Rect(100, 120, 150, 50)
        btn_join = pygame.Rect(350, 120, 150, 50)
        
        pygame.draw.rect(screen, (50, 150, 50), btn_host)
        pygame.draw.rect(screen, (50, 50, 150), btn_join)
        
        screen.blit(font.render("HOST", True, WHITE), (135, 130))
        screen.blit(font.render("JOIN", True, WHITE), (390, 130))
        
        # Поле IP
        txt_surf = font.render(text, True, color)
        width = max(200, txt_surf.get_width()+10)
        input_box.w = width
        screen.blit(txt_surf, (input_box.x+5, input_box.y+5))
        pygame.draw.rect(screen, color, input_box, 2)
        
        lbl_ip = font.render("IP Address:", True, WHITE)
        screen.blit(lbl_ip, (50, 210))

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None, None
                
            if event.type == pygame.MOUSEBUTTONDOWN:
                if btn_host.collidepoint(event.pos):
                    return "HOST", text
                if btn_join.collidepoint(event.pos):
                    return "JOIN", text
                    
                if input_box.collidepoint(event.pos):
                    active = not active
                else:
                    active = False
                color = color_active if active else color_inactive
                
            if event.type == pygame.KEYDOWN:
                if active:
                    if event.key == pygame.K_RETURN:
                        return "JOIN", text
                    elif event.key == pygame.K_BACKSPACE:
                        text = text[:-1]
                    else:
                        text += event.unicode
                        
        pygame.display.flip()

if __name__ == "__main__":
    mode, ip = main_menu()
    
    if mode == "HOST":
        pygame.quit() # Закрываем меню
        # Запускаем сервер
        server = GameServer()
        # Автоматически запускаем клиент для хоста
        threading.Thread(target=server.start, daemon=True).start()
        
        client = GameClient()
        client.connect('127.0.0.1')
        
    elif mode == "JOIN":
        pygame.quit()
        client = GameClient()
        client.connect(ip)

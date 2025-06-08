#!/usr/bin/env python3
"""
TUIO a PureData - Conversor de datos TUIO a formato OSC para PureData
Mantiene 20 puntos fijos y gestiona la asignación de IDs TUIO a estos puntos.
Versión mejorada para trabajar en red.
"""

import argparse
import time
import socket
from pythonosc import udp_client
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
import threading
import logging

# Configuración de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_local_ip():
    """Obtiene la IP local de la interfaz de red activa"""
    try:
        # Conecta a un servidor externo para determinar la IP local
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        return local_ip
    except Exception:
        return "127.0.0.1"

class TuioToPdConverter:
    def __init__(self, pd_host="127.0.0.1", pd_port=9001, tuio_host="0.0.0.0", tuio_port=3333):
        # Cliente OSC para enviar datos a PureData
        self.pd_client = udp_client.SimpleUDPClient(pd_host, pd_port)
        
        # Configuración del servidor TUIO
        self.dispatcher = Dispatcher()
        self.dispatcher.map("/tuio/2Dcur", self.handle_tuio_message)
        
        # Usar 0.0.0.0 para escuchar en todas las interfaces
        self.tuio_server = BlockingOSCUDPServer((tuio_host, tuio_port), self.dispatcher)
        
        # Número fijo de puntos a gestionar
        self.num_points = 20
        
        # Diccionario para mapear IDs TUIO a índices de punto (0-19)
        # { id_tuio: indice_punto }
        self.id_to_point = {}
        
        # Estado actual de los puntos
        # Lista de tuplas (x, y, vx, vy, accel) o None si el punto no está activo
        self.points = [(-1, -1, 0, 0, 0) for _ in range(self.num_points)]
        
        # Lista de IDs activas
        self.active_ids = set()
        
        # Contador para el envío periódico de datos
        self.last_send_time = time.time()
        self.send_interval = 0.01  # 10ms

    def handle_tuio_message(self, address, *args):
        """Maneja los mensajes TUIO recibidos"""
        if not args:
            return
        
        command = args[0]
        
        if command == "set":
            # Formato: set session_id x y x_vel y_vel accel
            if len(args) < 7:
                return
                
            session_id = int(args[1])
            x, y = float(args[2]), float(args[3])
            x_vel, y_vel = float(args[4]), float(args[5])
            accel = float(args[6])
            
            self.process_set(session_id, x, y, x_vel, y_vel, accel)
            
        elif command == "alive":
            # Obtener las IDs activas
            new_active_ids = set([int(args[i]) for i in range(1, len(args))])
            self.process_alive(new_active_ids)
            
        elif command == "fseq":
            # Al final de una secuencia de frames, enviar datos a PureData
            current_time = time.time()
            if current_time - self.last_send_time >= self.send_interval:
                self.send_to_puredata()
                self.last_send_time = current_time

    def process_set(self, session_id, x, y, x_vel, y_vel, accel):
        """Procesa un mensaje TUIO 'set'"""
        # Si la ID ya está asignada a un punto, actualizar ese punto
        if session_id in self.id_to_point:
            point_index = self.id_to_point[session_id]
            self.points[point_index] = (x, y, x_vel, y_vel, accel)
        else:
            # Si es una nueva ID, asignarle un punto libre
            self.assign_new_id(session_id, x, y, x_vel, y_vel, accel)

    def assign_new_id(self, session_id, x, y, x_vel, y_vel, accel):
        """Asigna una nueva ID TUIO a un punto libre"""
        # Buscar un punto libre (que tenga x=-1)
        for i in range(self.num_points):
            if self.points[i][0] == -1:
                self.id_to_point[session_id] = i
                self.points[i] = (x, y, x_vel, y_vel, accel)
                self.active_ids.add(session_id)
                logger.debug(f"Asignada ID {session_id} al punto {i}")
                return
                
        # Si no hay puntos libres, usar el primero (política FIFO)
        logger.warning("No hay puntos libres. Reemplazando el primero.")
        self.id_to_point[session_id] = 0
        self.points[0] = (x, y, x_vel, y_vel, accel)
        self.active_ids.add(session_id)

    def process_alive(self, new_active_ids):
        """Procesa un mensaje TUIO 'alive' con las IDs activas"""
        # Identificar IDs que ya no están activas
        removed_ids = self.active_ids - new_active_ids
        
        # Liberar los puntos asociados a IDs eliminadas
        for removed_id in removed_ids:
            if removed_id in self.id_to_point:
                point_index = self.id_to_point[removed_id]
                self.points[point_index] = (-1, -1, 0, 0, 0)  # Resetear el punto
                del self.id_to_point[removed_id]
                logger.debug(f"ID {removed_id} eliminada, punto {point_index} liberado")
        
        # Actualizar el conjunto de IDs activas
        self.active_ids = new_active_ids

    def send_to_puredata(self):
        """Envía el estado actual de los puntos a PureData usando OSC"""
        for i in range(self.num_points):
            x, y, vx, vy, accel = self.points[i]
            
            # Construir mensaje OSC: /point N X Y vX vY accel
            self.pd_client.send_message("/point", [i, x, y, vx, vy, accel])
            
        logger.debug("Datos enviados a PureData")

    def run(self):
        """Inicia el servidor TUIO"""
        local_ip = get_local_ip()
        logger.info(f"IP local detectada: {local_ip}")
        logger.info(f"Servidor TUIO escuchando en todas las interfaces (0.0.0.0:{self.tuio_server.server_address[1]})")
        logger.info(f"Para conectar desde otro dispositivo, usar IP: {local_ip}:{self.tuio_server.server_address[1]}")
        logger.info(f"Enviando datos a PureData en: {self.pd_client._address}:{self.pd_client._port}")
        
        thread = threading.Thread(target=self.tuio_server.serve_forever)
        thread.daemon = True
        thread.start()
        
        try:
            # Mantener el programa en ejecución
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Deteniendo servidor...")
            self.tuio_server.shutdown()

def main():
    parser = argparse.ArgumentParser(description="Conversor de TUIO a PureData")
    parser.add_argument("--pd-host", default="127.0.0.1", help="Host de PureData")
    parser.add_argument("--pd-port", type=int, default=9001, help="Puerto OSC de PureData")
    parser.add_argument("--tuio-host", default="0.0.0.0", help="Host para recibir TUIO (0.0.0.0 para todas las interfaces)")
    parser.add_argument("--tuio-port", type=int, default=3333, help="Puerto para recibir TUIO")
    parser.add_argument("--debug", action="store_true", help="Activar mensajes de depuración")
    parser.add_argument("--show-ip", action="store_true", help="Mostrar IP local y salir")
    
    args = parser.parse_args()
    
    if args.show_ip:
        local_ip = get_local_ip()
        print(f"Tu IP local es: {local_ip}")
        print(f"Configura tu dispositivo TUIO para enviar a: {local_ip}:{args.tuio_port}")
        return
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    converter = TuioToPdConverter(
        pd_host=args.pd_host,
        pd_port=args.pd_port,
        tuio_host=args.tuio_host,
        tuio_port=args.tuio_port
    )
    
    converter.run()

if __name__ == "__main__":
    main()
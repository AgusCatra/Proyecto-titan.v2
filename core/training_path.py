# core/training_path.py
from typing import List, Dict
from .behavior_analyzer import BehaviorProfile

class TrainingPath:
    """Generador de rutas de aprendizaje personalizadas."""
    
    def __init__(self):
        self.exercise_catalog = self._load_exercise_catalog()
    
    def generate_path(self, profile: BehaviorProfile, current_level: int = 1) -> List[Dict]:
        """Genera una ruta de aprendizaje personalizada."""
        
        path_mapping = {
            BehaviorProfile.APURADO: self._get_apurado_path,
            BehaviorProfile.SIN_NOCION_ESPACIO: self._get_spatial_path,
            BehaviorProfile.INEFICIENTE: self._get_efficiency_path,
            BehaviorProfile.NOVATO: self._get_novato_path,
            BehaviorProfile.EFICIENTE: self._get_advanced_path
        }
        
        return path_mapping.get(profile, self._get_novato_path)(current_level)
    
    def _load_exercise_catalog(self) -> Dict:
        """Catálogo de ejercicios disponibles."""
        return {
            'ambientacion': {
                'name': 'Módulo de Ambientación',
                'description': 'Reconocimiento de controles básicos',
                'duration': 30,
                'difficulty': 1
            },
            'control_velocidad': {
                'name': 'Control de Velocidad',
                'description': 'Ejercicios de control de velocidad y precisión',
                'duration': 45,
                'difficulty': 2
            },
            'maniobras_precision': {
                'name': 'Maniobras de Precisión',
                'description': 'Ejercicios de manejo espacial y precisión',
                'duration': 60,
                'difficulty': 3
            },
            'eficiencia_operativa': {
                'name': 'Eficiencia Operativa',
                'description': 'Optimización de tiempos y movimientos',
                'duration': 50,
                'difficulty': 3
            }
        }
    
    def _get_apurado_path(self, level: int) -> List[Dict]:
        """Ruta para perfil Apurado."""
        return [
            self.exercise_catalog['control_velocidad'],
            self.exercise_catalog['maniobras_precision']
        ]
    
    def _get_spatial_path(self, level: int) -> List[Dict]:
        """Ruta para perfil Sin Noción del Espacio."""
        return [
            self.exercise_catalog['maniobras_precision'],
            self.exercise_catalog['control_velocidad']
        ]
    
    def _get_efficiency_path(self, level: int) -> List[Dict]:
        """Ruta para perfil Ineficiente."""
        return [
            self.exercise_catalog['eficiencia_operativa'],
            self.exercise_catalog['control_velocidad']
        ]
    
    def _get_novato_path(self, level: int) -> List[Dict]:
        """Ruta para perfil Novato."""
        return [
            self.exercise_catalog['ambientacion'],
            self.exercise_catalog['control_velocidad'],
            self.exercise_catalog['maniobras_precision']
        ]
    
    def _get_advanced_path(self, level: int) -> List[Dict]:
        """Ruta para perfil Eficiente (avanzado)."""
        return [
            self.exercise_catalog['eficiencia_operativa']
        ]
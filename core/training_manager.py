# core/training_manager.py
from typing import Optional, Dict, List
from .db_manager import get_db_connection
from .behavior_analyzer import BehaviorAnalyzer, BehaviorProfile
from .training_path import TrainingPath

class TrainingManager:
    """Gestor principal del sistema de capacitación."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.analyzer = BehaviorAnalyzer()
        self.path_generator = TrainingPath()
    
    def evaluate_operator(self, operator_name: str) -> Optional[Dict]:
        """Evalúa un operador y genera su ruta de aprendizaje."""
        
        with get_db_connection(self.db_path) as conn:
            # Obtener última sesión del operador
            sessions = self._get_operator_sessions(conn, operator_name)
            
            if not sessions:
                return None
            
            latest_session = sessions[0]  # Más reciente
            summary_events = self._get_session_events(conn, latest_session['id_sesion'])
            
            # Analizar comportamiento
            profile = self.analyzer.analyze_session(dict(latest_session), summary_events)
            
            # Generar ruta de aprendizaje
            training_path = self.path_generator.generate_path(profile)
            
            return {
                'operator': operator_name,
                'profile': profile.value,
                'last_session': dict(latest_session),
                'training_path': training_path,
                'recommendation': self._generate_recommendation(profile)
            }
    
    def _get_operator_sessions(self, conn, operator_name: str) -> List:
        """Obtiene las sesiones de un operador ordenadas por fecha."""
        from .db_manager import get_sessions_by_operator
        return get_sessions_by_operator(conn, operator_name)
    
    def _get_session_events(self, conn, session_id: int) -> List:
        """Obtiene los eventos de una sesión."""
        from .db_manager import get_summary_events_by_session
        return [dict(event) for event in get_summary_events_by_session(conn, session_id)]
    
    def _generate_recommendation(self, profile: BehaviorProfile) -> str:
        """Genera una recomendación basada en el perfil."""
        
        recommendations = {
            BehaviorProfile.APURADO: "Enfócate en la precisión antes que en la velocidad. Practica ejercicios de control de velocidad.",
            BehaviorProfile.SIN_NOCION_ESPACIO: "Desarrolla tu percepción espacial con ejercicios de maniobras de precisión.",
            BehaviorProfile.INEFICIENTE: "Mejora tu eficiencia operativa. Practica movimientos fluidos y planificación de rutas.",
            BehaviorProfile.NOVATO: "Comienza con el módulo de ambientación para familiarizarte con los controles básicos.",
            BehaviorProfile.EFICIENTE: "¡Excelente trabajo! Continúa con ejercicios avanzados para mantener tu nivel."
        }
        
        return recommendations.get(profile, "Continúa practicando para mejorar tus habilidades.")


# Ejemplo de uso del sistema de capacitación
if __name__ == "__main__":
    # Ejemplo de cómo usar el sistema
    training_manager = TrainingManager('database/titan.db')
    
    # Evaluar un operador
    evaluation = training_manager.evaluate_operator('Agustin')
    
    if evaluation:
        print(f"Operador: {evaluation['operator']}")
        print(f"Perfil: {evaluation['profile']}")
        print(f"Recomendación: {evaluation['recommendation']}")
        print("Ruta de entrenamiento:")
        for exercise in evaluation['training_path']:
            print(f"  - {exercise['name']}: {exercise['description']}")
    else:
        print("No se encontraron datos para el operador.")
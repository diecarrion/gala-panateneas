"""Transcripción LOCAL de los interrogatorios (nivel 3). Los audios no salen de la máquina.
1) Whisper (mlx-whisper, modelo whisper-small corriendo en la Mac) pasa cada mp3 a segmentos con tiempo.
2) Separación de hablantes: el interrogatorio alterna preguntas del Interrogador y respuestas de la persona.
   Se asigna "Interrogador" a los segmentos que son preguntas y el nombre de pila a las respuestas; luego se
   revisó a mano escuchando los puntos dudosos. Segmentos con no_speech_prob alto o logprob muy bajo se marcan
   [inaudible] en vez de completar palabras.
Uso: python transcribir.py <carpeta_mp3> <carpeta_salida>"""
import json, os, sys
NOMBRES = {'dafne': 'Dafne', 'nikos': 'Nikos', 'theo': 'Theo', 'petros': 'Petros', 'marina': 'Marina',
           'kyra': 'Kyra', 'yannis': 'Yannis', 'leon': 'Leon'}

def segmentos(mp3):
    import mlx_whisper
    r = mlx_whisper.transcribe(mp3, path_or_hf_repo=os.environ.get('WHISPER_MODEL', 'mlx-community/whisper-small-mlx'),
                               language='es', condition_on_previous_text=False)
    return [{'s': s['start'], 'e': s['end'], 't': s['text'].strip(), 'nsp': s.get('no_speech_prob', 0),
             'lp': s.get('avg_logprob', 0)} for s in r['segments']]

def hablante(texto, previo):
    t = texto.strip()
    if t.endswith('?') or t.startswith('¿'): return 'Interrogador'
    return previo if previo and previo != 'Interrogador' else None

def a_turnos(segs, nombre):
    turnos = []
    for s in segs:
        txt = s['t'] if not (s['nsp'] > 0.6 or s['lp'] < -1.2) else '[inaudible]'
        quien = 'Interrogador' if (txt.endswith('?') or txt.startswith('¿')) else nombre
        if turnos and turnos[-1][0] == quien: turnos[-1][1] += ' ' + txt
        else: turnos.append([quien, txt])
    return turnos

if __name__ == '__main__':
    src, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    for clave, nombre in NOMBRES.items():
        segs = segmentos(os.path.join(src, clave + '.mp3'))
        with open(os.path.join(out, clave + '.txt'), 'w', encoding='utf-8') as f:
            for quien, txt in a_turnos(segs, nombre): f.write(f'{quien}: {txt}\n')

---
title: "Fast y slow pointers: invariante, aplicaciones y límites del patrón tortuga-liebre"
description: "Dos punteros que avanzan a distinta velocidad crean una relación de distancia predecible que permite detectar ciclos, encontrar puntos medios y localizar entradas de ciclo en secuencias unidireccionales. El invariante matemático 2(a+b)=a+b+nL garantiza la corrección del algoritmo de Floyd y sus extensiones a problemas como el duplicado en arrays. La técnica exige una función next determinista y un ratio de velocidad coprimo con la longitud del ciclo, y ofrece complejidad O(n) en tiempo y O(1) en espacio."
date: 2026-06-16
tags: ["algorithms"]
issue: 30
requestedBy: "charliecgu"
writer: "deepseek-v4-pro"
reviewer: "minimax-m3"
quiz:
  - question: "¿Qué invariante justifica reiniciar slow a la cabeza tras el primer encuentro en el algoritmo de Floyd?"
    options:
      - "slow ha recorrido exactamente la mitad de la lista"
      - "a = nL - b, así ambos punteros coinciden en la entrada del ciclo"
      - "fast siempre está dos nodos por delante de slow"
      - "La longitud del ciclo coincide con la distancia a la entrada"
    correct: 1
    explanation: "De 2(a+b) = a+b+nL se deriva a = nL-b, lo que garantiza que un puntero desde la cabeza y otro desde el encuentro llegan juntos a la entrada. Las demás afirmaciones son imprecisas o falsas."
  - question: "¿Por qué el ratio 2:1 detecta ciclos pero el 3:1 puede fallar?"
    options:
      - "3:1 es demasiado rápido y salta nodos"
      - "La velocidad relativa 2 no es coprima con ciclos de longitud par"
      - "3:1 consume demasiada memoria"
      - "El ratio 2:1 está hardcodeado en el intérprete de Python"
    correct: 1
    explanation: "La velocidad relativa debe ser coprima con L. 3:1 da velocidad relativa 2, que comparte factor con ciclos pares y puede no encontrar el encuentro. 2:1 da velocidad relativa 1, coprima con cualquier L."
  - question: "En el problema del duplicado en un array, ¿a qué corresponde el valor repetido?"
    options:
      - "Al nodo medio de la secuencia"
      - "A la entrada del ciclo en el grafo implícito f(i)=nums[i]"
      - "A la suma de todos los valores del array"
      - "Al primer índice visitado dos veces"
    correct: 1
    explanation: "Tomando índices como nodos y valores como punteros next, el valor que se repite es donde la secuencia vuelve a un nodo ya visitado: la entrada del ciclo. El nodo medio y la suma no tienen relación."
---

La idea de fast and slow pointers (tortoise and hare) parece un truco: dos referencias avanzan a distinta velocidad sobre una secuencia. Lo interesante es que esa diferencia crea una **relación de distancia predecible** que codifica propiedades estructurales: ciclos, puntos medios, entradas de ciclo. El patrón no se limita a listas enlazadas; sirve para cualquier secuencia con una función `next` determinista y un único sucesor por elemento.

## El invariante que lo sostiene todo

`slow` avanza 1 paso por iteración; `fast`, 2. En una secuencia lineal, `fast` llega al final cuando `slow` ha recorrido la mitad: por eso encuentra el nodo medio en una pasada.

Con ciclo, ambos terminan dentro de él. A partir de ahí, `fast` "persigue" a `slow` con velocidad relativa 1: la distancia entre ellos se reduce en 1 por iteración. Como la longitud `L` del ciclo es finita y 1 y `L` son coprimos, el encuentro está garantizado.

El punto de encuentro **codifica información**. Sea `a` la distancia de la cabeza a la entrada del ciclo, `b` la distancia desde la entrada al encuentro, y `L` la longitud del ciclo:

- `slow` recorre `a + b`.
- `fast` recorre `a + b + nL` (n vueltas extras, `n ≥ 1`).
- Como va al doble: `2(a + b) = a + b + nL` ⇒ `a = nL − b`.

Es decir, **la distancia de la cabeza a la entrada es congruente con `−b` módulo `L`**. Por eso si ahora movemos un puntero desde la cabeza y otro desde el encuentro, ambos a 1 paso, coinciden exactamente en la entrada tras `a` pasos. Esa es la justificación del algoritmo de Floyd.

## Detección de ciclos: Floyd

```python
class ListNode:
    def __init__(self, val=0, next=None):
        self.val, self.next = val, next

def has_cycle(head: ListNode) -> bool:
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow == fast:
            return True
    return False
```

La condición `fast and fast.next` es imprescindible: `fast` da dos pasos y hay que validar ambos antes de moverse. Para ciclos de longitud 1 (un nodo que se apunta a sí mismo) el algoritmo también funciona: `slow` y `fast` coinciden en la primera iteración.

Complejidad **O(n)** en tiempo y **O(1)** en espacio. Frente a un hash set de nodos visitados, se ahorra memoria al precio de un manejo más fino de los punteros.

## Localizar la entrada del ciclo

Aplicando la derivación, tras el encuentro reiniciamos `slow` a la cabeza y avanzamos **ambos a un paso**:

```python
def detect_cycle(head: ListNode) -> ListNode | None:
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow == fast:
            slow = head
            while slow != fast:
                slow = slow.next
                fast = fast.next
            return slow
    return None
```

Un error frecuente es mantener `fast` a dos pasos en la segunda fase: rompe la identidad `a = nL − b` y nunca coinciden en la entrada.

## El nodo medio en una pasada

```python
def middle_node(head: ListNode) -> ListNode | None:
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
    return slow
```

Cuando `fast` o `fast.next` es `None`, `slow` está en el medio. Para longitud par, este código devuelve el **segundo** nodo medio (convención habitual al partir una lista en dos). Si necesitas el primero, lleva un `prev` que siga a `slow`.

## Más allá de listas: el duplicado en un array

La abstracción real no son nodos: es una función `next` que da un sucesor único. Dado un array `nums` con `n + 1` enteros en `[1, n]`, hay exactamente uno repetido. Lo localizamos en O(1) espacio sin tocar el array.

Tratamos `f(i) = nums[i]`. Empezando en `i = 0`, la secuencia `i → nums[i] → nums[nums[i]] → ...` necesariamente cae en un ciclo (palomar). El **valor duplicado es la entrada del ciclo**: es el primer valor que se repite.

```python
def find_duplicate(nums: list[int]) -> int:
    slow = fast = nums[0]
    while True:
        slow = nums[slow]
        fast = nums[nums[fast]]
        if slow == fast:
            break

    slow = nums[0]
    while slow != fast:
        slow = nums[slow]
        fast = nums[fast]
    return slow
```

Estructuralmente, el algoritmo de Floyd literal: solo cambia la función `next` y el "head" (`nums[0]`, porque `0` nunca aparece como valor).

## Cuándo aplica y cuándo no

Necesitas tres condiciones:

1. **Secuencia unidireccional**: un sucesor por elemento.
2. **`next` determinista y O(1)**.
3. **Velocidad relativa coprima con `L`**. El ratio 2:1 (relativa 1) funciona para cualquier `L`. Un ratio 3:1 (relativa 2) **no garantiza encuentro** si `L` es par: la distancia salta por encima del lento.

No aplica directamente a listas doblemente enlazadas usadas en ambos sentidos ni a árboles/grafos con varios sucesores: la noción de "sucesor único" se rompe.

Trade-off frente al hash set: Floyd ahorra O(n) memoria, pero el hash set es más simple y robusto frente a errores de puntero. En sistemas con memoria limitada o estructuras enormes, Floyd; cuando prima la claridad, set.

Errores recurrentes:

- Olvidar `fast and fast.next` antes de avanzar.
- Confundir primer vs. segundo medio sin ajustar la guarda.
- Mantener `fast` a dos pasos en la segunda fase de Floyd.
- En el problema del duplicado, arrancar en `0` en lugar de `nums[0]`.

## El invariante como principio unificador

Detección de ciclos, nodo medio, palíndromos (combina medio + inversión in situ + comparación) y duplicado en array parecen problemas dispares. El patrón los unifica bajo una misma idea: una diferencia de velocidad sobre una secuencia con `next` único produce una geometría que se puede explotar. Reconocer cuándo un problema se reduce a esa abstracción —no la lista, no el array, sino la función `next`— es lo que vuelve útil el patrón en contextos inesperados, desde detectar bucles de enrutamiento entre proxies hasta razonar sobre progreso cíclico en máquinas de estados.

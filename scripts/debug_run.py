"""Local debug runner for the BQ FinOps agents.

Usage (from repo root):

    uv run python scripts/debug_run.py
    uv run python scripts/debug_run.py --agent metadata
    uv run python scripts/debug_run.py --agent baseline "SELECT 1"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.DEBUG)

from app.agent import root_agent  # noqa: E402
from app.agents.pipeline import (  # noqa: E402
    baseline_agent,
    formatter_agent,
    metadata_agent,
    optimizer_agent,
    validator_agent,
)

AGENTS = {
    "root": root_agent,
    "metadata": metadata_agent,
    "baseline": baseline_agent,
    "optimizer": optimizer_agent,
    "validator": validator_agent,
    "formatter": formatter_agent,
}

PROMPT = """
Otimize a seguinte query:

WITH

-- 1. IDENTIFICA A ÚLTIMA DATA DISPONÍVEL DE CADA MÊS (GLOBAL) ULTIMAS_DATAS_MES AS ( SELECT DATE_TRUNC(DATA_VISAO, DAY) AS mes_referencia, MAX(DATA_VISAO) AS ultima_data_mes FROM gc-pr-dl-001.dtl_bl_refined.TB_CARTOES_HIST_DIA WHERE STATUS_CONVENIO = 'A' AND STATUS_CONTA = 'ATIVO' -- AND CODIGO_CONVENIO NOT IN UNNEST(CONVENIOS_PRE) -- AND COD_GRUPO_CONVENIO NOT IN UNNEST(GRUPOS_OUT) AND TRIM(STATUS_CARTAO) <> 'CANCELADO'
AND funcionalidade <> 'PRE-PAGO' AND DATE(DATA_VISAO) >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 42 DAY) AND EXTRACT(DAYOFWEEK FROM DATE(DATA_VISAO)) NOT IN (1, 7) GROUP BY 1 ),

-- 2. BASE DE CONTAS FILTRADA PELAS DATAS DE FECHAMENTO base_contas AS ( SELECT DISTINCT A.DATA_VISAO AS REFERENCIA, A.ID_CONTA, B.DATA_CANCELAMENTO_CONTA, FROM gc-pr-dl-001.dtl_bl_refined.TB_CARTOES_HIST_DIA A INNER JOIN ULTIMAS_DATAS_MES U ON A.DATA_VISAO = U.mes_referencia LEFT JOIN gc-pr-dl-001-cred.dtl_bl_refined.VM_CONTAS_NOVA B ON B.ID_CONTA = A.ID_CONTA WHERE A.STATUS_CONVENIO = 'A' -- AND A.CODIGO_CONVENIO NOT IN UNNEST(CONVENIOS_PRE) -- AND A.COD_GRUPO_CONVENIO NOT IN UNNEST(GRUPOS_OUT) AND A.STATUS_CONTA = 'ATIVO' AND TRIM(A.STATUS_CARTAO) <> 'CANCELADO'
AND A.funcionalidade <> 'PRE-PAGO' AND DATE(A.DATA_VISAO) >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 42 DAY) AND EXTRACT(DAYOFWEEK FROM DATE(A.DATA_VISAO)) NOT IN (1, 7) ),

SCORE_UNIFICADO AS (
    SELECT
        ID_CONTA,
        DT_CARGA AS DT_REF,
        CASE WHEN FL_FILTRO1 = 1 OR FL_FILTRO4 = 1 THEN 1 ELSE 0 END AS FL_S_SCORE,
        NR_SCORE_COM_SCR as PROD_SCORE_COM_SCR,
        NR_SCORE_SEM_SCR as PROD_SCORE_SEM_SCR,
        FL_FILTRO1 AS PROD_FILTROS1,
        FL_FILTRO4 AS PROD_FILTROS4,
        FX_SCORE_COM_SCR,
        FX_SCORE_SEM_SCR,
        IF(NM_PERFIL_SCR IS NOT NULL, 1, 0) AS PROD_FL_SCR,
        FX_SCORE_FINAL,
        MAX(CASE WHEN NM_PERFIL_SCR IS NOT NULL THEN 1 ELSE 0 END) AS FL_SCR
    FROM `gc-pr-dl-001-cred.modelagem.RF_SCORE_APPLICATION_OUTPUT` -- Tabela do Score Ref. de Produção Atual
    GROUP BY ALL
),

    SCORE_BACKTEST_PROD AS (
    SELECT
        *,
        -- Criação da janela de vigência do score utilizando a lógica de Turnover (LAG)
        IFNULL(LAG(DT_REF) OVER(PARTITION BY ID_CONTA ORDER BY DT_REF DESC), CURRENT_DATETIME()) AS DT_REF_ATE
    FROM SCORE_UNIFICADO
),

SEGMENTOS_UNICOS AS (
    SELECT DISTINCT
        IDGRUPOCONVENIO AS ID_GRUPO_CONVENIO,
        MIN(B.SEGMENTO) AS SEGMENTO,
        CASE WHEN MIN(DATACADASTRO) >= '2025-12-01' THEN 'NOVO' ELSE 'LEGADO' END AS FL_LEGADO_NOVO
    FROM `gc-pr-dl-001.dtl_bl_raw.T_LOJA` A
    LEFT JOIN `gc-pr-dl-001-cred.CREDITO_COBRANCA_PF.SEGMENTOS_CONVENIO_PF` B ON B.ID_CONVENIO = A.IDLOJA
    WHERE IDGRUPOCONVENIO IS NOT NULL
    GROUP BY ALL
)

-- 3. QUERY FINAL: RELATÓRIO DE AUDITORIA E COMPARAÇÃO
SELECT
A.REFERENCIA,
A.DATA_CANCELAMENTO_CONTA,
-- Cálculo da Maturação da Perda (MOB) baseado na data base de primeira compra do cliente
        CASE
            WHEN DATE_DIFF(DATE(A.DATA_CANCELAMENTO_CONTA), DATE(A.REFERENCIA), DAY) <= 30 THEN 0
            ELSE DIV(DATE_DIFF(DATE(A.DATA_CANCELAMENTO_CONTA), DATE(A.REFERENCIA), DAY) - 1, 30)
        END AS MOB,
B.NOME_GRUPO_CONVENIO,
  -- Agregação dos conceitos de negócios

            CASE WHEN DATE_DIFF(DATE(A.REFERENCIA), IFNULL(B.DATA_ADMISSAO, B.DATA_CADASTRO_CONTA), DAY) <= 90 THEN 1 ELSE 0 END AS FLAG_MEN_90D,

            CASE
              WHEN DATE_DIFF(DATE(A.REFERENCIA), IFNULL(DATE(B.DATA_ADMISSAO), DATE(B.DATA_CADASTRO_CONTA)), MONTH) <= 1  THEN '01. Até 1 mes'
              WHEN DATE_DIFF(DATE(A.REFERENCIA), IFNULL(DATE(B.DATA_ADMISSAO), DATE(B.DATA_CADASTRO_CONTA)), MONTH) <= 2  THEN '02. De 1 - 2 meses'
              WHEN DATE_DIFF(DATE(A.REFERENCIA), IFNULL(DATE(B.DATA_ADMISSAO), DATE(B.DATA_CADASTRO_CONTA)), MONTH) <= 3  THEN '03. De 2 - 3 meses'
              WHEN DATE_DIFF(DATE(A.REFERENCIA), IFNULL(DATE(B.DATA_ADMISSAO), DATE(B.DATA_CADASTRO_CONTA)), MONTH) <= 6  THEN '04. De 3 - 6 meses'
              WHEN DATE_DIFF(DATE(A.REFERENCIA), IFNULL(DATE(B.DATA_ADMISSAO), DATE(B.DATA_CADASTRO_CONTA)), MONTH) <= 12 THEN '05. De 6 - 12 meses'
              WHEN DATE_DIFF(DATE(A.REFERENCIA), IFNULL(DATE(B.DATA_ADMISSAO), DATE(B.DATA_CADASTRO_CONTA)), MONTH) <= 18 THEN '06. De 12 - 18 meses'
              WHEN DATE_DIFF(DATE(A.REFERENCIA), IFNULL(DATE(B.DATA_ADMISSAO), DATE(B.DATA_CADASTRO_CONTA)), MONTH) <= 24 THEN '07. De 18 - 24 meses'
              ELSE '08. Acima de 24 meses'
            END AS FAIXA_TEMPO_ADMISSAO,

            IFNULL(C.FL_LEGADO_NOVO, 'LEGADO') AS FL_LEGADO_NOVO,

            -- Blocos de risco (Regras de Negócio)
            CASE
              WHEN IFNULL(C.FL_LEGADO_NOVO, 'LEGADO') = 'LEGADO' AND IF(DATE_DIFF(DATE(A.REFERENCIA), IFNULL(B.DATA_ADMISSAO, B.DATA_CADASTRO_CONTA), DAY) <= 90, 1, 0) = 0 AND IFNULL(P.FX_SCORE_FINAL, 12) IN (1, 2, 3, 12) THEN 'ALTO_RISCO_LEGADO'
              WHEN IFNULL(C.FL_LEGADO_NOVO, 'LEGADO') = 'NOVO'   AND IF(DATE_DIFF(DATE(A.REFERENCIA), IFNULL(B.DATA_ADMISSAO, B.DATA_CADASTRO_CONTA), DAY) <= 90, 1, 0) = 0 AND IFNULL(P.FX_SCORE_FINAL, 12) IN (1, 2, 3, 4, 12) THEN 'ALTO_RISCO_NOVO'
              WHEN IF(DATE_DIFF(DATE(A.REFERENCIA), IFNULL(B.DATA_ADMISSAO, B.DATA_CADASTRO_CONTA), DAY) <= 90, 1, 0) = 1          AND IFNULL(P.FX_SCORE_FINAL, 12) IN (1, 2, 3, 4, 12)                                 THEN 'ALTO_RISCO_<90DIAS'
              ELSE 'BAIXO_RISCO'
            END AS CLASSE_RISCO_PRODUCAO,
  -- ==========================================
  -- VISÃO PRODUÇÃO (SCORE_UNIFICADO)
  -- ==========================================
  P.FX_SCORE_COM_SCR,
  P.FX_SCORE_SEM_SCR,
  IFNULL(P.FX_SCORE_FINAL, 12) AS FX_SCORE_FINAL,

  COUNT(DISTINCT A.ID_CONTA) AS QTD_CONTAS,
  COUNT(DISTINCT
                CASE WHEN A.DATA_CANCELAMENTO_CONTA IS NOT NULL THEN A.ID_CONTA ELSE NULL END) AS QTD_MAUS,

FROM BASE_CONTAS                                        A                                                                                                         LEFT JOIN
     SCORE_BACKTEST_PROD                                P ON P.ID_CONTA = A.ID_CONTA AND DATE(A.REFERENCIA) > P.DT_REF AND DATE(A.REFERENCIA) <= P.DT_REF_ATE     LEFT JOIN
     `gc-pr-dl-001-cred.dtl_bl_refined.VM_CONTAS_NOVA`  B ON B.ID_CONTA = A.ID_CONTA                                                                              LEFT JOIN
     SEGMENTOS_UNICOS                                   C ON C.ID_GRUPO_CONVENIO = B.ID_GRUPO_CONVENIO

GROUP BY ALL
"""


async def main(prompt: str, agent_name: str) -> None:
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        user_id="debug",
        app_name="debug",
    )
    runner = Runner(
        agent=AGENTS[agent_name],
        session_service=session_service,
        app_name="debug",
    )
    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    )
    async for event in runner.run_async(
        user_id="debug",
        session_id=session.id,
        new_message=message,
    ):
        print(event)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Debug-run a BQ FinOps agent.")
    parser.add_argument("prompt", nargs="?", default=PROMPT)
    parser.add_argument("--agent", choices=AGENTS, default="root")
    args = parser.parse_args()
    asyncio.run(main(args.prompt, args.agent))

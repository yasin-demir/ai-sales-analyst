"""General CSV/XLSX workflow: python -m src.cli --help."""
import argparse
from datetime import date
from dataclasses import asdict
import json
from pathlib import Path
from .importing import ImportConfig,inspect_file
from .general_reports import write_report
from .questions import QUESTION_CATALOG
from .question_io import answer_file_question

def main(argv=None):
    parser=argparse.ArgumentParser(description='Local sales analysis for reviewed CSV/XLSX mappings.')
    commands=parser.add_subparsers(dest='command',required=True)
    inspect=commands.add_parser('inspect',help='Preview and suggest mappings; no analysis')
    inspect.add_argument('file',type=Path);inspect.add_argument('--config',type=Path)
    inspect.add_argument('--template',type=Path,help='Save an unreviewed mapping draft')
    analyze=commands.add_parser('analyze',help='Validate input and generate an HTML report')
    analyze.add_argument('file',type=Path);analyze.add_argument('--config',type=Path,required=True)
    analyze.add_argument('--output',type=Path,required=True);analyze.add_argument('--json-output',type=Path)
    analyze.add_argument('--confirm-mapping',action='store_true',help='Confirm this mapping after review')
    analyze.add_argument('--save-config',type=Path,help='Save reviewed settings for reuse')
    analyze.add_argument('--filters',type=Path,help='JSON date/segment filter specification')
    analyze.add_argument('--comparison',type=Path,help='JSON baseline/current date bounds')
    commands.add_parser('questions',help='Show supported question intents and requirements')
    ask=commands.add_parser('ask',help='Execute one explicit analytical question and save a scoped JSON answer')
    ask.add_argument('file',type=Path);ask.add_argument('--config',type=Path,required=True)
    ask.add_argument('--request',type=Path,required=True);ask.add_argument('--output',type=Path,required=True)
    for name,help_text in [('compare-months','Compare the last two completed calendar months without AI'),
                           ('ask-ai','Interpret a question using OpenAI or a local Ollama model')]:
        command=commands.add_parser(name,help=help_text)
        command.add_argument('file',type=Path)
        command.add_argument('--config',type=Path,required=True)
        command.add_argument('--reference-date',required=True,help='Explicit business-local date: YYYY-MM-DD')
        command.add_argument('--output',type=Path,help='Optional scoped JSON answer (includes source and warnings)')
        if name=='ask-ai':
            command.add_argument('--question',required=True)
            command.add_argument('--provider',choices=['openai','ollama'],default='openai',help='Use ollama for local inference without an API key')
            command.add_argument('--model',help='Model name; defaults to the selected provider setting')
    args=parser.parse_args(argv)
    if args.command=='questions':
        print(json.dumps(QUESTION_CATALOG,ensure_ascii=False,indent=2));return 0
    try:
        config=ImportConfig.load(args.config) if args.config else ImportConfig()
        protected={args.file.resolve()}
        if args.config:protected.add(args.config.resolve())
        for name in ('request','filters','comparison'):
            value=getattr(args,name,None)
            if value:protected.add(value.resolve())
        outputs=[getattr(args,name,None) for name in ('template','output','json_output','save_config')]
        outputs=[p for p in outputs if p]
        if len({p.resolve() for p in outputs})!=len(outputs):raise ValueError('Output paths must be distinct.')
        # Explicit --save-config may update that configuration, but no report may overwrite it.
        for target in outputs:
            if target.resolve()==args.file.resolve() or (target.resolve() in protected and target!=getattr(args,'save_config',None)):
                raise ValueError('Outputs may not overwrite the input or mapping configuration.')
        if args.command in ('compare-months','ask-ai'):
            from .sales_assistant import READY,execute_decision
            reference_date=date.fromisoformat(args.reference_date)
            if reference_date.isoformat()!=args.reference_date:
                raise ValueError('Reference date must use YYYY-MM-DD.')
            if args.command=='ask-ai':
                if args.provider=='ollama':
                    from .intent_ollama import answer_natural_language
                else:
                    from .intent_ai import answer_natural_language
                result=answer_natural_language(args.file,config,args.question,reference_date,model=args.model)
            else:
                result=execute_decision(args.file,config,READY,reference_date)
            if args.output:
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
            print(result['text'])
            return 0 if result['status']=='ok' else 2
        if args.command=='inspect':
            preview=inspect_file(args.file,config)
            if args.template:
                draft=ImportConfig(**{**asdict(config),'mapping':preview['suggestions'],'mapping_reviewed':False})
                draft.save(args.template)
            print(json.dumps(preview,ensure_ascii=False,indent=2,default=str));return 0
        if args.command=='ask':
            request=json.loads(args.request.read_text(encoding='utf-8-sig'))
            answer=answer_file_question(args.file,config,request)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(answer,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
            print(f'Answer: {args.output.resolve()} | status: {answer["status"]}')
            return 0 if answer['status'] in ('ok','partial','empty','empty_ranking') else 2
        if args.confirm_mapping:config.mapping_reviewed=True
        filters=json.loads(args.filters.read_text(encoding='utf-8-sig')) if args.filters else None
        comparison=json.loads(args.comparison.read_text(encoding='utf-8-sig')) if args.comparison else None
        result=write_report(args.file,config,args.output,args.json_output,filters=filters,comparison=comparison)
        if args.save_config and config.mapping_reviewed:config.save(args.save_config)
        print(f'Report: {args.output.resolve()}')
        print(f'Readiness: {result["quality"]["status"]} | input records: {result["quality"]["input_rows"]} | analyzed: {result["quality"]["analyzed_rows"]}')
        if result['quality']['status']!='ready':
            print('Only the quality report was generated. Correct the reported issues or explicitly configure invalid_rows=exclude.')
            return 2
        if 'selection' in result:print(f'Selected records: {result["selection"]["selected_records"]}')
        print(f'Currency sections: {len(result["partitions"])}; no currencies were combined.');return 0
    except (ValueError,OSError,LookupError,ImportError,RuntimeError) as exc:
        print(f'Could not complete the request: {exc}');return 1

if __name__=='__main__':raise SystemExit(main())
